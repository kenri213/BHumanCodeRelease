/**
 * @file RealSenseProvider.cpp
 *
 * This file implements a module that grabs images from the RealSense sensor.
 *
 * @author Thomas Röfer
 */

#include "RealSenseProvider.h"
#include "Math/Range.h"
#include "Platform/BHAssert.h"
#include "Platform/BoosterAppCommissioning.h"
#include "Platform/SystemCall.h"
#include "Platform/Thread.h"
#include "Platform/Time.h"
#include <chrono>
#include <cstring>
#include <memory>
#include <sstream>
#include <stdexcept>
#ifdef TARGET_ROBOT
#include <librealsense2/h/rs_pipeline.h>
#include <librealsense2/h/rs_frame.h>
#endif

MAKE_MODULE(RealSenseProvider);

thread_local RealSenseProvider* RealSenseProvider::theInstance = nullptr;

const Rangei RealSenseProvider::settingLimits[RealSenseProvider::numOfSettings]
{
  {0, 1},
  {-64, 64},
  {0, 100},
  {1, 10000},
  {0, 128},
  {100, 500},
  {-180, 180},
  {0, 100},
  {0, 100},
  {2800, 6500},
  {0, 1},
  {0, 1},
  {0, 32},
  {0, 3},
  {0, 1}
};

const std::unordered_map<RealSenseProvider::Setting, RealSenseProvider::Setting> RealSenseProvider::skipIfEnabled =
{
  {RealSenseProvider::exposure, RealSenseProvider::autoExposure},
  {RealSenseProvider::gain, RealSenseProvider::autoExposure},
  {RealSenseProvider::whiteBalance, RealSenseProvider::autoWhiteBalance}
};

#ifdef TARGET_ROBOT
const rs2_option RealSenseProvider::options[RealSenseProvider::numOfSettings] =
{
  RS2_OPTION_BACKLIGHT_COMPENSATION,
  RS2_OPTION_BRIGHTNESS,
  RS2_OPTION_CONTRAST,
  RS2_OPTION_EXPOSURE,
  RS2_OPTION_GAIN,
  RS2_OPTION_GAMMA,
  RS2_OPTION_HUE,
  RS2_OPTION_SATURATION,
  RS2_OPTION_SHARPNESS,
  RS2_OPTION_WHITE_BALANCE,
  RS2_OPTION_ENABLE_AUTO_EXPOSURE,
  RS2_OPTION_ENABLE_AUTO_WHITE_BALANCE,
  RS2_OPTION_FRAMES_QUEUE_SIZE,
  RS2_OPTION_POWER_LINE_FREQUENCY,
  RS2_OPTION_AUTO_EXPOSURE_PRIORITY
};
#endif

void RealSenseProvider::Settings::read(In& stream)
{
  FOREACH_ENUM(Setting, setting)
    switch(setting)
    {
      case backlightCompensation:
      case autoExposure:
      case autoWhiteBalance:
      case autoExposurePriority:
      {
        bool value = static_cast<bool>((*this)[setting]);
        Streaming::streamIt(stream, TypeRegistry::getEnumName(setting), value);
        (*this)[setting] = static_cast<int>(value);
        break;
      }
      case powerLineFrequency:
      {
        PowerLineFrequency value = static_cast<PowerLineFrequency>((*this)[setting]);
        Streaming::streamIt(stream, TypeRegistry::getEnumName(setting), value);
        (*this)[setting] = static_cast<int>(value);
        break;
      }
      default:
        Streaming::streamIt(stream, TypeRegistry::getEnumName(setting), (*this)[setting]);
    }
}

void RealSenseProvider::Settings::write(Out& stream) const
{
  FOREACH_ENUM(Setting, setting)
    switch(setting)
    {
      case backlightCompensation:
      case autoExposure:
      case autoWhiteBalance:
      case autoExposurePriority:
      {
        Streaming::streamIt(stream, TypeRegistry::getEnumName(setting), static_cast<bool>((*this)[setting]));
        break;
      }
      case powerLineFrequency:
      {
        Streaming::streamIt(stream, TypeRegistry::getEnumName(setting), static_cast<PowerLineFrequency>((*this)[setting]));
        break;
      }
      default:
        Streaming::streamIt(stream, TypeRegistry::getEnumName(setting), (*this)[setting]);
    }
}

void RealSenseProvider::Settings::reg()
{
  PUBLISH(reg);
  REG_CLASS(Settings);
  FOREACH_ENUM(Setting, setting)
    switch(setting)
    {
      case backlightCompensation:
      case autoExposure:
      case autoWhiteBalance:
      case autoExposurePriority:
        TypeRegistry::addAttribute(_type, typeid(bool).name(), TypeRegistry::getEnumName(setting));
        break;
      case powerLineFrequency:
        TypeRegistry::addAttribute(_type, typeid(PowerLineFrequency).name(), TypeRegistry::getEnumName(setting));
        break;
      default:
        TypeRegistry::addAttribute(_type, typeid(int).name(), TypeRegistry::getEnumName(setting));
    }
}

RealSenseProvider::RealSenseProvider()
{
  if(!readCameraIntrinsics() || !readCameraResolution())
    throw std::runtime_error("RealSenseProvider: missing camera intrinsics/resolution configuration");
  InMapFile stream("realSenseCamera.cfg");
  if(stream.exists())
    stream >> streamConfig;
  if(!streamConfig.maxFrameWaitMs || !streamConfig.maxMetadataAgeMs ||
     (streamConfig.enableDepthKeepAlive && (streamConfig.depthWidth <= 0 || streamConfig.depthHeight <= 0 || streamConfig.depthFrameRate <= 0)))
    throw std::runtime_error("RealSenseProvider: invalid realSenseCamera.cfg");

  // Initialize applied settings with a value illegal for all settings.
  std::fill(appliedSettings.begin(), appliedSettings.end(), 20000);

#ifdef TARGET_ROBOT
  // Create a context object. This object owns the handles to all connected RealSense devices.
  try
  {
    context = rs2_create_context(RS2_API_VERSION, &e);
    check("create context");
    pipeline = rs2_create_pipeline(context, &e);
    check("create pipeline");
    config = rs2_create_config(&e);
    check("create stream config");
    setupCamera();
  }
  catch(...)
  {
    stopStream();
    if(config) rs2_delete_config(config);
    if(pipeline) rs2_delete_pipeline(pipeline);
    if(context) rs2_delete_context(context);
    throw;
  }
#else
  setupCamera();
#endif
  theInstance = this;
}

RealSenseProvider::~RealSenseProvider()
{
  stopStream();

#ifdef TARGET_ROBOT
  // Release RealSense resources.
  if(config) rs2_delete_config(config);
  if(pipeline) rs2_delete_pipeline(pipeline);
  if(context) rs2_delete_context(context);
#endif

  theInstance = nullptr;
}

void RealSenseProvider::update(CameraImage& theCameraImage)
{
#ifdef TARGET_ROBOT
  // The framework constructs the provider during its first module execution;
  // Perception::afterFrame() has not called waitForFrameData() at that point.
  // Bootstrap with the same bounded, validated acquisition used afterwards.
  if(!yuvFrameData)
    waitForFrameData2();
  if(!yuvFrameData)
    throw std::runtime_error("RealSenseProvider: refusing to publish a fresh timestamp on a missing color image");
  if(!rectificationMap.empty())
  {
    theCameraImage.setResolution(cameraInfo.width / 2, cameraInfo.height);
    RealSenseCamera::rectifyYuyv(yuvFrameData, cameraInfo.width, cameraInfo.height, frameStride,
                                rectificationMap, reinterpret_cast<uint8_t*>(theCameraImage[0]));
  }
  else if(frameStride == cameraInfo.width * 2)
    theCameraImage.setReference(cameraInfo.width / 2, cameraInfo.height, const_cast<uint8_t*>(yuvFrameData));
  else
  {
    theCameraImage.setResolution(cameraInfo.width / 2, cameraInfo.height);
    for(int y = 0; y < cameraInfo.height; ++y)
      std::memcpy(theCameraImage[y], yuvFrameData + static_cast<std::size_t>(y) * frameStride, cameraInfo.width * 2);
  }
  theCameraImage.timestamp = imageTimestamp;
  // Count only the first successful CameraImage delivery for a real frame,
  // never stream construction, a rejected frame, or desktop placeholder data.
  if(BoosterAppCommissioning::enabled() && imageTimestamp != lastImageTimestamp)
    BoosterAppCommissioning::noteCameraFrame();
  lastImageTimestamp = imageTimestamp;
#else
  // Desktop simulation supplies its own camera; this branch never opens USB devices.
  theCameraImage.setResolution(cameraInfo.width / 2, cameraInfo.height);
  theCameraImage.timestamp = Time::getCurrentSystemTime();
#endif
}

void RealSenseProvider::update(JPEGImage& theJPEGImage)
{
  theJPEGImage.fromCameraImage(theCameraImage, jpegQuality);
}

bool RealSenseProvider::readCameraIntrinsics()
{
  InMapFile stream("cameraIntrinsics.cfg");
  bool exist = stream.exists();
  if(exist)
    stream >> cameraIntrinsics;
  return exist;
}

bool RealSenseProvider::readCameraResolution()
{
  InMapFile stream("cameraResolution.cfg");
  bool exist = stream.exists();
  if(exist)
    stream >> cameraResolutionRequest;
  return exist;
}

bool RealSenseProvider::processResolutionRequest()
{
  const CameraResolutionRequest::Resolution requestedResolution = theCameraResolutionRequest.resolution;
  if(SystemCall::getMode() == SystemCall::Mode::physicalRobot && requestedResolution != lastResolutionRequest)
  {
    lastResolutionRequest = requestedResolution;
    switch(requestedResolution)
    {
      case CameraResolutionRequest::noRequest:
        return false;
      case CameraResolutionRequest::defaultRes:
        if(!readCameraResolution())
          cameraResolutionRequest.resolution = CameraResolutionRequest::w640h480f30;
        return true;
      case CameraResolutionRequest::w424h240f5:
      case CameraResolutionRequest::w424h240f15:
      case CameraResolutionRequest::w424h240f30:
      case CameraResolutionRequest::w424h240f60:
      case CameraResolutionRequest::w424h240f90:
      case CameraResolutionRequest::w480h270f5:
      case CameraResolutionRequest::w480h270f15:
      case CameraResolutionRequest::w480h270f30:
      case CameraResolutionRequest::w480h270f60:
      case CameraResolutionRequest::w480h270f90:
      case CameraResolutionRequest::w640h360f5:
      case CameraResolutionRequest::w640h360f15:
      case CameraResolutionRequest::w640h360f30:
      case CameraResolutionRequest::w640h360f60:
      case CameraResolutionRequest::w640h360f90:
      case CameraResolutionRequest::w640h480f5:
      case CameraResolutionRequest::w640h480f15:
      case CameraResolutionRequest::w640h480f30:
      case CameraResolutionRequest::w640h480f60:
      case CameraResolutionRequest::w848h480f5:
      case CameraResolutionRequest::w848h480f15:
      case CameraResolutionRequest::w848h480f30:
      case CameraResolutionRequest::w848h480f60:
      case CameraResolutionRequest::w1280h720f5:
      case CameraResolutionRequest::w1280h720f15:
      case CameraResolutionRequest::w1280h720f30:
      case CameraResolutionRequest::w1280h800f5:
      case CameraResolutionRequest::w1280h800f15:
      case CameraResolutionRequest::w1280h800f30:
        cameraResolutionRequest.resolution = requestedResolution;
        return true;
      default:
        FAIL("Unknown resolution.");
        return false;
    }
  }
  else
    return false;
}

void RealSenseProvider::applySettings()
{
  FOREACH_ENUM(Setting, setting)
  {
    const auto skipCheck = skipIfEnabled.find(setting);
    if(settings[setting] != appliedSettings[setting]
       && (skipCheck == skipIfEnabled.end() || appliedSettings[skipCheck->second] != 1))
    {
      const int limitedSetting = settingLimits[setting].limit(settings[setting]);
      if(limitedSetting != settings[setting])
        OUTPUT_WARNING(TypeRegistry::getEnumName(setting) << " should be inside [" << settingLimits[setting].min
                       << ", " << settingLimits[setting].max << "], but is " << settings[setting]);
#ifdef TARGET_ROBOT
      auto* optionSensor = reinterpret_cast<rs2_options*>(sensor);
      const bool supported = rs2_supports_option(optionSensor, options[setting], &e) != 0;
      check("query supported RGB option");
      const bool readOnly = supported && rs2_is_option_read_only(optionSensor, options[setting], &e) != 0;
      check("query RGB option writability");
      if(supported && !readOnly)
      {
        float minimum, maximum, step, defaultValue;
        rs2_get_option_range(optionSensor, options[setting], &minimum, &maximum, &step, &defaultValue, &e);
        check("query RGB option range");
        float value = std::clamp(static_cast<float>(limitedSetting), minimum, maximum);
        if(step > 0.f)
          value = std::clamp(minimum + std::round((value - minimum) / step) * step, minimum, maximum);
        rs2_set_option(optionSensor, options[setting], value, &e);
        check("set RGB option");
      }
      else
        OUTPUT_WARNING("RealSenseProvider: unsupported/read-only RGB option " << TypeRegistry::getEnumName(setting) << ", skipped");
#endif
      appliedSettings[setting] = settings[setting];
    }
  }
}

void RealSenseProvider::setupCamera()
{
  // A stale per-robot X5 resolution must fail explicitly rather than start a wrong network/profile.
  cameraResolutionRequest.apply(cameraInfo);
  if(cameraInfo.width <= 0 || cameraInfo.width % 2 || cameraInfo.width > 2560 || cameraInfo.height <= 0 ||
     cameraInfo.height > 1920 || cameraInfo.frameRate <= 0)
    throw std::runtime_error("RealSenseProvider: invalid color stream resolution");

  // set opening angle
  cameraInfo.openingAngleWidth = cameraIntrinsics.openingAngleWidth;
  cameraInfo.openingAngleHeight = cameraIntrinsics.openingAngleHeight;

  // set optical center
  cameraInfo.opticalCenter.x() = cameraIntrinsics.opticalCenter.x() * cameraInfo.width;
  cameraInfo.opticalCenter.y() = cameraIntrinsics.opticalCenter.y() * cameraInfo.height;

  // update focal length
  cameraInfo.updateFocalLength();

  startStream();
}

void RealSenseProvider::startStream()
{
#ifdef TARGET_ROBOT
  rs2_device_list* rawDevices = rs2_query_devices(context, &e);
  check("enumerate devices");
  std::unique_ptr<rs2_device_list, decltype(&rs2_delete_device_list)> devices(rawDevices, rs2_delete_device_list);
  const int count = rs2_get_device_count(devices.get(), &e);
  check("count devices");
  if(count == 0 || (count > 1 && streamConfig.serialNumber.empty()))
    throw std::runtime_error("RealSenseProvider: no camera, or multiple cameras without an explicit serialNumber");

  rs2_config_disable_all_streams(config, &e);
  check("clear stream configuration");
  if(!streamConfig.serialNumber.empty())
  {
    rs2_config_enable_device(config, streamConfig.serialNumber.c_str(), &e);
    check("select camera serial number");
  }
  rs2_config_enable_stream(config, RS2_STREAM_COLOR, 0, cameraInfo.width, cameraInfo.height, RS2_FORMAT_YUYV, cameraInfo.frameRate, &e);
  check("configure YUYV color stream");
  // Upstream keepalive workaround; depth is NOT used for ball distance/localization.
  if(streamConfig.enableDepthKeepAlive)
  {
    rs2_config_enable_stream(config, RS2_STREAM_DEPTH, 0, streamConfig.depthWidth, streamConfig.depthHeight,
                             RS2_FORMAT_Z16, streamConfig.depthFrameRate, &e);
    check("configure depth keepalive stream");
  }
  pipelineProfile = rs2_pipeline_start_with_config(pipeline, config, &e);
  check("start stream (check USB permissions, supported RGB/depth profiles, and competing camera processes)");
  streamStarted = true;
  readDeviceIntrinsics();

  std::unique_ptr<rs2_device, decltype(&rs2_delete_device)> device(rs2_pipeline_profile_get_device(pipelineProfile, &e), rs2_delete_device);
  check("get streaming device");
  std::unique_ptr<rs2_sensor_list, decltype(&rs2_delete_sensor_list)> sensors(rs2_query_sensors(device.get(), &e), rs2_delete_sensor_list);
  check("query sensors");
  const int numOfSensors = rs2_get_sensors_count(sensors.get(), &e);
  check("count sensors");
  for(int i = 0; i < numOfSensors; ++i)
  {
    std::unique_ptr<rs2_sensor, decltype(&rs2_delete_sensor)> candidate(rs2_create_sensor(sensors.get(), i, &e), rs2_delete_sensor);
    check("create sensor handle");
    std::unique_ptr<rs2_stream_profile_list, decltype(&rs2_delete_stream_profiles_list)> profiles(
      rs2_get_stream_profiles(candidate.get(), &e), rs2_delete_stream_profiles_list);
    check("query sensor profiles");
    const int profileCount = rs2_get_stream_profiles_count(profiles.get(), &e);
    check("count sensor profiles");
    for(int j = 0; j < profileCount; ++j)
    {
      const auto* profile = rs2_get_stream_profile(profiles.get(), j, &e);
      check("get sensor profile");
      rs2_stream stream;
      rs2_format format;
      int index, uniqueId, fps;
      rs2_get_stream_profile_data(profile, &stream, &format, &index, &uniqueId, &fps, &e);
      check("read sensor profile");
      if(stream == RS2_STREAM_COLOR)
      {
        if(!sensor)
          sensor = candidate.release();
        break;
      }
    }
    // RGB-only operation must not issue depth/HDR control transfers. On D455
    // these can block inside the SDK even though the color stream is active.
    if(candidate && streamConfig.enableDepthKeepAlive)
    {
      auto* options = reinterpret_cast<rs2_options*>(candidate.get());
      const bool emitter = rs2_supports_option(options, RS2_OPTION_EMITTER_ENABLED, &e) != 0;
      check("query emitter support");
      if(emitter)
      {
        rs2_set_option(options, RS2_OPTION_EMITTER_ENABLED, 0.f, &e);
        check("disable depth emitter");
      }
    }
  }
  if(!sensor)
    throw std::runtime_error("RealSenseProvider: selected device has no color sensor");
  std::fill(appliedSettings.begin(), appliedSettings.end(), 20000);
  hasFrameNumber = false;
  fallbackTimestampReported = false;
#endif
}

void RealSenseProvider::stopStream()
{
#ifdef TARGET_ROBOT
  releaseFrames();
  if(sensor)
  {
    rs2_delete_sensor(sensor);
    sensor = nullptr;
  }
  if(streamStarted)
  {
    rs2_pipeline_stop(pipeline, &e);
    ok();
    streamStarted = false;
  }
  if(pipelineProfile)
  {
    rs2_delete_pipeline_profile(pipelineProfile);
    pipelineProfile = nullptr;
  }
#endif
}

void RealSenseProvider::waitForFrameData2()
{
#ifdef TARGET_ROBOT
  const bool hadFrame = yuvFrameData != nullptr;
  releaseFrames();
  if(processResolutionRequest())
  {
    stopStream();
    setupCamera();
  }
  applySettings();
  const auto start = std::chrono::steady_clock::now();
  acquisitionDiagnostics.beginWait(Time::getSystemTimeBase(), Time::getCurrentSystemTime());
  while(!yuvFrameData)
  {
    if(Thread::getCurrentThread() && !Thread::getCurrentThread()->isRunning())
      return;
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();
    acquisitionDiagnostics.observeHost(Time::getSystemTimeBase(), Time::getCurrentSystemTime());
    if(elapsed >= streamConfig.maxFrameWaitMs)
    {
      const auto& d = acquisitionDiagnostics;
      std::ostringstream evidence;
      evidence << "RealSenseProvider: color frame timeout; no synthetic image or X5 fallback published"
               << "; wait_ms=" << elapsed << " received=" << d.received << " color=" << d.color
               << " duplicate=" << d.duplicate << " backward=" << d.backward << " stale=" << d.stale
               << " metadata_not_advancing=" << d.metadataNotAdvancing << " clock_regression=" << d.clockRegression
               << " host_not_advancing=" << d.hostNotAdvancing << " observed_clock_regressions=" << d.observedClockRegressions
               << " last_received_valid=" << d.hasReceived << " last_received_frame=" << d.receivedNumber
               << " received_metadata_valid=" << d.receivedMetadata << " received_arrival=" << d.receivedArrival
               << " received_host_ms=" << d.receivedHost << " received_epoch_ms=" << d.receivedEpoch
               << " last_timestamp_rejection=" << RealSenseCamera::rejectionName(d.lastRejection)
               << " last_accepted_valid=" << d.hasAccepted << " last_accepted_frame=" << d.acceptedNumber
               << " accepted_metadata_valid=" << d.acceptedMetadata << " accepted_arrival=" << d.acceptedArrival
               << " accepted_host_ms=" << d.acceptedHost << " accepted_epoch_ms=" << d.acceptedEpoch
               << " accepted_timestamp_ms=" << d.acceptedTimestamp << " last_image_timestamp_ms=" << lastImageTimestamp
               << " timeout_host_ms=" << d.observedHost << " timeout_epoch_ms=" << d.observedEpoch;
      throw std::runtime_error(evidence.str());
    }
    const unsigned remaining = streamConfig.maxFrameWaitMs - static_cast<unsigned>(elapsed);
    const unsigned timeout = std::min(remaining, static_cast<unsigned>(std::max(1, hadFrame ? minWaitForImage : maxWaitForImage)));
    const bool received = rs2_pipeline_try_wait_for_frames(pipeline, &frames, timeout, &e) != 0;
    check("wait for frames");
    if(!received)
      continue;
    ++acquisitionDiagnostics.received;
    const int numOfFrames = rs2_embedded_frames_count(frames, &e);
    check("count frames");
    for(int i = numOfFrames - 1; i >= 0 && !yuvFrameData; --i)
    {
      frame = rs2_extract_frame(frames, i, &e);
      check("extract frame");
      const auto* profile = rs2_get_frame_stream_profile(frame, &e);
      check("read frame profile");
      rs2_stream stream;
      rs2_format format;
      int index, uniqueId, fps;
      rs2_get_stream_profile_data(profile, &stream, &format, &index, &uniqueId, &fps, &e);
      check("read frame format");
      if(stream == RS2_STREAM_COLOR)
      {
        const int width = rs2_get_frame_width(frame, &e);
        check("read color width");
        const int height = rs2_get_frame_height(frame, &e);
        check("read color height");
        frameStride = rs2_get_frame_stride_in_bytes(frame, &e);
        check("read color stride");
        const int size = rs2_get_frame_data_size(frame, &e);
        check("read color size");
        if(format != RS2_FORMAT_YUYV || width != cameraInfo.width || height != cameraInfo.height ||
           frameStride < width * 2 || size < frameStride * (height - 1) + width * 2)
          throw std::runtime_error("RealSenseProvider: unexpected color format/dimensions/stride");
        const auto number = rs2_get_frame_number(frame, &e);
        check("read frame number");
        bool metadata = rs2_supports_frame_metadata(frame, RS2_FRAME_METADATA_TIME_OF_ARRIVAL, &e) != 0;
        check("query timestamp metadata");
        rs2_metadata_type arrival = 0;
        if(metadata)
        {
          arrival = rs2_get_frame_metadata(frame, RS2_FRAME_METADATA_TIME_OF_ARRIVAL, &e);
          metadata = ok(false) && arrival > 0;
        }
        const auto base = Time::getSystemTimeBase();
        const auto now = Time::getCurrentSystemTime();
        const auto arrivalMs = arrival > 0 ? static_cast<std::uint64_t>(arrival) : 0;
        const auto stamp = RealSenseCamera::timestamp(metadata, arrivalMs, base, now,
                                                     lastImageTimestamp, streamConfig.maxMetadataAgeMs);
        acquisitionDiagnostics.noteColor(number, metadata, arrivalMs, base, now, hasFrameNumber, lastFrameNumber, stamp);
        if((!hasFrameNumber || number > lastFrameNumber) && stamp.source != RealSenseCamera::TimestampSource::rejected)
        {
          yuvFrameData = static_cast<const uint8_t*>(rs2_get_frame_data(frame, &e));
          check("get color pixels");
          imageTimestamp = stamp.value;
          lastFrameNumber = number;
          hasFrameNumber = true;
          acquisitionDiagnostics.noteAccepted(imageTimestamp);
          if(stamp.source == RealSenseCamera::TimestampSource::hostArrival && !fallbackTimestampReported)
          {
            OUTPUT_WARNING("RealSenseProvider: timestamp source=hostArrival (metadata missing/invalid clock); verify camera-motion alignment");
            fallbackTimestampReported = true;
          }
          if(!hadFrame)
            SystemCall::say("Camera ready");
        }
      }
      if(!yuvFrameData)
      {
        rs2_release_frame(frame);
        frame = nullptr;
      }
    }
    if(!yuvFrameData)
      releaseFrames();
  }
#endif
}

#ifdef TARGET_ROBOT
void RealSenseProvider::releaseFrames()
{
  if(frame) rs2_release_frame(frame);
  if(frames) rs2_release_frame(frames);
  frame = frames = nullptr;
  yuvFrameData = nullptr;
}

void RealSenseProvider::check(const char* operation)
{
  if(e)
  {
    const std::string message = std::string("RealSenseProvider: ") + operation + ": " + rs2_get_error_message(e);
    rs2_free_error(e);
    e = nullptr;
    throw std::runtime_error(message);
  }
}

void RealSenseProvider::readDeviceIntrinsics()
{
  std::unique_ptr<rs2_stream_profile_list, decltype(&rs2_delete_stream_profiles_list)> profiles(
    rs2_pipeline_profile_get_streams(pipelineProfile, &e), rs2_delete_stream_profiles_list);
  check("get active stream profiles");
  const int count = rs2_get_stream_profiles_count(profiles.get(), &e);
  check("count active profiles");
  for(int i = 0; i < count; ++i)
  {
    const auto* profile = rs2_get_stream_profile(profiles.get(), i, &e);
    check("get active profile");
    rs2_stream stream;
    rs2_format format;
    int index, uniqueId, fps;
    rs2_get_stream_profile_data(profile, &stream, &format, &index, &uniqueId, &fps, &e);
    check("read active profile");
    if(stream != RS2_STREAM_COLOR)
      continue;
    rs2_intrinsics intrinsics;
    rs2_get_video_stream_intrinsics(profile, &intrinsics, &e);
    check("read factory COLOR intrinsics");
    const RealSenseCamera::Intrinsics k{intrinsics.width, intrinsics.height, intrinsics.fx, intrinsics.fy, intrinsics.ppx, intrinsics.ppy};
    if(!k.valid() || k.width != cameraInfo.width || k.height != cameraInfo.height ||
       fps != cameraInfo.frameRate || format != RS2_FORMAT_YUYV)
      throw std::runtime_error("RealSenseProvider: invalid/mismatched factory color intrinsics/profile");
    std::array<float, 5> coefficients;
    std::copy(std::begin(intrinsics.coeffs), std::end(intrinsics.coeffs), coefficients.begin());
    if(!std::all_of(coefficients.begin(), coefficients.end(), [](float c) { return std::isfinite(c); }))
      throw std::runtime_error("RealSenseProvider: non-finite distortion coefficients");
    const bool zero = std::all_of(coefficients.begin(), coefficients.end(), [](float c) { return c == 0.f; });
    const bool brownFamily = intrinsics.model == RS2_DISTORTION_BROWN_CONRADY ||
                             intrinsics.model == RS2_DISTORTION_MODIFIED_BROWN_CONRADY ||
                             intrinsics.model == RS2_DISTORTION_INVERSE_BROWN_CONRADY;
    rectificationMap.clear();
    if(intrinsics.model == RS2_DISTORTION_BROWN_CONRADY && !zero && streamConfig.rectifyBrownConrady)
      rectificationMap = RealSenseCamera::brownMap(k, coefficients);
    else if(intrinsics.model == RS2_DISTORTION_INVERSE_BROWN_CONRADY && !zero && streamConfig.rectifyBrownConrady)
      rectificationMap = RealSenseCamera::inverseBrownMap(k, coefficients);
    else if(!((intrinsics.model == RS2_DISTORTION_NONE && zero) || (brownFamily && zero)))
      throw std::runtime_error(std::string("RealSenseProvider: unsupported/non-rectified RGB distortion: ") + rs2_distortion_to_string(intrinsics.model));

    cameraIntrinsics.openingAngleWidth = k.openingAngleWidth();
    cameraIntrinsics.openingAngleHeight = k.openingAngleHeight();
    cameraIntrinsics.opticalCenter = {k.cx / k.width, k.cy / k.height};
    cameraInfo.openingAngleWidth = cameraIntrinsics.openingAngleWidth;
    cameraInfo.openingAngleHeight = cameraIntrinsics.openingAngleHeight;
    cameraInfo.opticalCenter = {k.cx, k.cy};
    cameraInfo.updateFocalLength();
    OUTPUT_TEXT("RealSenseProvider: COLOR " << k.width << "x" << k.height << "@" << fps <<
                " factory fx=" << k.fx << " fy=" << k.fy << " cx=" << k.cx << " cy=" << k.cy <<
                " distortion=" << rs2_distortion_to_string(intrinsics.model) << " rectified=" << !rectificationMap.empty());
    return;
  }
  throw std::runtime_error("RealSenseProvider: active pipeline has no COLOR stream");
}
#endif

bool RealSenseProvider::ok([[maybe_unused]] const bool print)
{
#ifdef TARGET_ROBOT
  if(e)
  {
    if(print)
      OUTPUT_ERROR("RealSenseProvider: " << rs2_get_error_message(e));
    rs2_free_error(e);
    e = nullptr;
    return false;
  }
  else
#endif
    return true;
}

bool RealSenseProvider::isFrameDataComplete()
{
#ifdef TARGET_ROBOT
  if(theInstance)
    return theInstance->yuvFrameData != nullptr;
  else
#endif
    return true;
}

void RealSenseProvider::waitForFrameData()
{
#ifdef TARGET_ROBOT
  if(theInstance)
    theInstance->waitForFrameData2();
#endif
}
