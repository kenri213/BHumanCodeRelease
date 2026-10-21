// Independent camera diagnostics. No Booster SDK, ROS, motion/mode publisher,
// installation, process killing, network configuration or X5 operations.
#include "diagnostic_options.h"
#include <librealsense2/rs.h>
#include <librealsense2/h/rs_pipeline.h>
#include <librealsense2/h/rs_config.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <memory>
#include <string>

namespace
{
  volatile std::sig_atomic_t interrupted = 0;
  void cancel(int) { interrupted = 1; }

  struct Api
  {
    rs2_error* error = nullptr;
    ~Api() { if(error) rs2_free_error(error); }
    void check(const char* operation)
    {
      if(error)
      {
        const std::string message = std::string(operation) + ": " + rs2_get_error_message(error);
        rs2_free_error(error);
        error = nullptr;
        throw std::runtime_error(message);
      }
    }
  };

  template<typename T, void (*Delete)(T*)>
  using Handle = std::unique_ptr<T, decltype(Delete)>;

  std::string info(Api& api, const rs2_device* device, rs2_camera_info key)
  {
    const bool supported = rs2_supports_device_info(device, key, &api.error) != 0;
    api.check("query device info support");
    if(!supported) return "unavailable";
    const char* value = rs2_get_device_info(device, key, &api.error);
    api.check("read device info");
    return value ? value : "unavailable";
  }

  void intrinsics(Api& api, const rs2_stream_profile* profile)
  {
    rs2_intrinsics i{};
    rs2_get_video_stream_intrinsics(profile, &i, &api.error);
    api.check("read active video intrinsics");
    if(i.width <= 0 || i.height <= 0 || !std::isfinite(i.fx) || !std::isfinite(i.fy) ||
       i.fx <= 0.f || i.fy <= 0.f || !std::isfinite(i.ppx) || !std::isfinite(i.ppy) ||
       i.ppx < 0.f || i.ppx >= i.width || i.ppy < 0.f || i.ppy >= i.height)
      throw std::runtime_error("Invalid factory video intrinsics");
    std::cout << "  INTRINSICS " << i.width << 'x' << i.height
              << " fx=" << i.fx << " fy=" << i.fy << " cx=" << i.ppx << " cy=" << i.ppy
              << " distortion=" << rs2_distortion_to_string(i.model) << " coeffs=[";
    for(unsigned n = 0; n < 5; ++n)
    {
      if(!std::isfinite(i.coeffs[n])) throw std::runtime_error("Nonfinite distortion coefficient");
      std::cout << (n ? "," : "") << i.coeffs[n];
    }
    std::cout << "]\n";
  }

  struct Profiles { bool color = false; bool depth = false; };
  Profiles enumerate(Api& api, const rs2_device* device, bool all)
  {
    Handle<rs2_sensor_list, rs2_delete_sensor_list> sensors(rs2_query_sensors(device, &api.error), rs2_delete_sensor_list);
    api.check("enumerate sensors");
    const int sensorCount = rs2_get_sensors_count(sensors.get(), &api.error);
    api.check("count sensors");
    Profiles found;
    for(int s = 0; s < sensorCount; ++s)
    {
      Handle<rs2_sensor, rs2_delete_sensor> sensor(rs2_create_sensor(sensors.get(), s, &api.error), rs2_delete_sensor);
      api.check("open sensor descriptor (no streaming)");
      Handle<rs2_stream_profile_list, rs2_delete_stream_profiles_list> list(
        rs2_get_stream_profiles(sensor.get(), &api.error), rs2_delete_stream_profiles_list);
      api.check("enumerate profiles");
      const int count = rs2_get_stream_profiles_count(list.get(), &api.error);
      api.check("count profiles");
      std::cout << "SENSOR index=" << s << " profile_count=" << count << '\n';
      for(int p = 0; p < count; ++p)
      {
        const auto* profile = rs2_get_stream_profile(list.get(), p, &api.error);
        api.check("read profile");
        rs2_stream stream;
        rs2_format format;
        int index, id, fps;
        rs2_get_stream_profile_data(profile, &stream, &format, &index, &id, &fps, &api.error);
        api.check("read profile type");
        if(stream != RS2_STREAM_COLOR && stream != RS2_STREAM_DEPTH)
        {
          if(all) std::cout << "  PROFILE " << rs2_stream_to_string(stream) << " format=" << rs2_format_to_string(format) << " fps=" << fps << '\n';
          continue;
        }
        int width, height;
        rs2_get_video_stream_resolution(profile, &width, &height, &api.error);
        api.check("read profile dimensions");
        const bool color = stream == RS2_STREAM_COLOR && index == 0 && format == RS2_FORMAT_YUYV && width == 640 && height == 480 && fps == 30;
        const bool depth = stream == RS2_STREAM_DEPTH && index == 0 && format == RS2_FORMAT_Z16 && width == 424 && height == 240 && fps == 5;
        found.color |= color;
        found.depth |= depth;
        if(all || color || depth)
        {
          std::cout << "  PROFILE " << rs2_stream_to_string(stream) << " index=" << index << ' ' << width << 'x' << height
                    << " format=" << rs2_format_to_string(format) << " fps=" << fps << " target=" << (color || depth) << '\n';
          if(color || depth) intrinsics(api, profile);
        }
      }
    }
    return found;
  }

  struct Pipeline
  {
    rs2_pipeline* pipe = nullptr;
    bool running = false;
    explicit Pipeline(rs2_context* context, Api& api)
    {
      pipe = rs2_create_pipeline(context, &api.error);
      api.check("create pipeline (no streaming)");
    }
    void stop()
    {
      if(running)
      {
        rs2_error* error = nullptr;
        rs2_pipeline_stop(pipe, &error);
        running = false;
        if(error)
        {
          const std::string message = rs2_get_error_message(error);
          rs2_free_error(error);
          throw std::runtime_error("stop camera stream: " + message);
        }
      }
    }
    ~Pipeline()
    {
      try { stop(); }
      catch(const std::exception& error) { std::cerr << "CLEANUP_ERROR " << error.what() << '\n'; }
      if(pipe) rs2_delete_pipeline(pipe);
    }
  };

  void capture(Api& api, Pipeline& pipeline, rs2_config* config, const D455Diagnostic::Options& options)
  {
    std::cout << "CAPTURE_EXPLICIT frames=" << options.frames << " deadline_ms=" << options.timeoutMs
              << " motion=NONE camera_options=UNCHANGED\n" << std::flush;
    const auto start = std::chrono::steady_clock::now();
    Handle<rs2_pipeline_profile, rs2_delete_pipeline_profile> active(
      rs2_pipeline_start_with_config(pipeline.pipe, config, &api.error), rs2_delete_pipeline_profile);
    api.check("start selected camera (if busy, close its owner manually; this tool never kills processes)");
    pipeline.running = true;
    unsigned count = 0;
    unsigned long long lastNumber = 0;
    bool haveNumber = false;
    while(count < options.frames)
    {
      if(interrupted) throw std::runtime_error("Capture interrupted; stopping camera normally");
      const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();
      if(elapsed >= options.timeoutMs) throw std::runtime_error("Capture deadline reached before requested fresh COLOR frames");
      const auto remaining = options.timeoutMs - static_cast<unsigned>(elapsed);
      rs2_frame* raw = nullptr;
      const bool received = rs2_pipeline_try_wait_for_frames(pipeline.pipe, &raw, std::min(remaining, 200u), &api.error) != 0;
      Handle<rs2_frame, rs2_release_frame> frames(raw, rs2_release_frame);
      api.check("wait for camera frames");
      if(!received) continue;
      const int embedded = rs2_embedded_frames_count(frames.get(), &api.error);
      api.check("count embedded frames");
      for(int f = 0; f < embedded; ++f)
      {
        Handle<rs2_frame, rs2_release_frame> frame(rs2_extract_frame(frames.get(), f, &api.error), rs2_release_frame);
        api.check("extract frame");
        const auto* profile = rs2_get_frame_stream_profile(frame.get(), &api.error);
        api.check("read captured profile");
        rs2_stream stream;
        rs2_format format;
        int index, id, fps;
        rs2_get_stream_profile_data(profile, &stream, &format, &index, &id, &fps, &api.error);
        api.check("read captured format");
        if(stream != RS2_STREAM_COLOR) continue;
        const int width = rs2_get_frame_width(frame.get(), &api.error); api.check("frame width");
        const int height = rs2_get_frame_height(frame.get(), &api.error); api.check("frame height");
        const int stride = rs2_get_frame_stride_in_bytes(frame.get(), &api.error); api.check("frame stride");
        const int size = rs2_get_frame_data_size(frame.get(), &api.error); api.check("frame size");
        const auto number = rs2_get_frame_number(frame.get(), &api.error); api.check("frame number");
        const double timestamp = rs2_get_frame_timestamp(frame.get(), &api.error); api.check("frame timestamp");
        const auto domain = rs2_get_frame_timestamp_domain(frame.get(), &api.error); api.check("frame timestamp domain");
        if(format != RS2_FORMAT_YUYV || width != 640 || height != 480 || fps != 30 ||
           stride < width * 2 || size < static_cast<std::int64_t>(stride) * (height - 1) + width * 2 || !std::isfinite(timestamp))
          throw std::runtime_error("Captured COLOR format/dimensions/stride/timestamp mismatch");
        const void* pixels = rs2_get_frame_data(frame.get(), &api.error); api.check("read frame data");
        if(!pixels) throw std::runtime_error("Captured null COLOR pixels");
        if(haveNumber && number <= lastNumber) continue;
        lastNumber = number;
        haveNumber = true;
        ++count;
        const bool metadata = rs2_supports_frame_metadata(frame.get(), RS2_FRAME_METADATA_TIME_OF_ARRIVAL, &api.error) != 0;
        api.check("query arrival metadata support");
        rs2_metadata_type arrival = 0;
        if(metadata) { arrival = rs2_get_frame_metadata(frame.get(), RS2_FRAME_METADATA_TIME_OF_ARRIVAL, &api.error); api.check("arrival metadata"); }
        if(count == 1) intrinsics(api, profile);
        if(count <= 5 || count == options.frames)
          std::cout << "FRAME count=" << count << " number=" << number << " stride=" << stride << " bytes=" << size
                    << " timestamp_ms=" << timestamp << " domain=" << rs2_timestamp_domain_to_string(domain)
                    << " arrival_metadata=" << (metadata ? std::to_string(arrival) : "unavailable") << '\n';
      }
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();
    pipeline.stop();
    std::cout << "CAPTURE_OK fresh_color_frames=" << count << " elapsed_ms=" << elapsed << " stream_stopped=true\n";
  }
}

int main(int argc, char** argv)
{
  try
  {
    const auto options = D455Diagnostic::parse(std::vector<std::string>(argv + 1, argv + argc));
    if(options.help)
    {
      std::cout << "d455_probe [--serial SERIAL] [--all-profiles] [--color-only] [--timeout-ms 250..30000]\n"
                   "           [--capture [--frames 1..300]]\n"
                   "Default: enumerate devices/profiles/intrinsics and resolve target config WITHOUT starting streams.\n"
                   "Targets: COLOR 640x480 YUYV30 + optional DEPTH 424x240 Z16@5 keepalive.\n"
                   "--capture explicitly opens ONLY the selected camera for bounded frames/deadline.\n"
                   "No motion, SDK mode changes, camera option setters, X5 access, or process killing.\n";
      return EXIT_SUCCESS;
    }
    std::signal(SIGINT, cancel);
    std::signal(SIGTERM, cancel);
    std::cout << std::fixed << std::setprecision(6) << "MODE=" << (options.capture ? "explicit_capture" : "passive_enumeration") << " motion=NONE\n";
    Api api;
    Handle<rs2_context, rs2_delete_context> context(rs2_create_context(RS2_API_VERSION, &api.error), rs2_delete_context);
    api.check("create librealsense context");
    Handle<rs2_device_list, rs2_delete_device_list> devices(rs2_query_devices(context.get(), &api.error), rs2_delete_device_list);
    api.check("enumerate devices");
    const int count = rs2_get_device_count(devices.get(), &api.error); api.check("count devices");
    std::cout << "DEVICE_COUNT=" << count << '\n';
    Handle<rs2_device, rs2_delete_device> selected(nullptr, rs2_delete_device);
    std::string selectedSerial;
    Profiles target;
    for(int d = 0; d < count; ++d)
    {
      Handle<rs2_device, rs2_delete_device> device(rs2_create_device(devices.get(), d, &api.error), rs2_delete_device);
      api.check("read device descriptor");
      const auto serial = info(api, device.get(), RS2_CAMERA_INFO_SERIAL_NUMBER);
      std::cout << "DEVICE name=" << info(api, device.get(), RS2_CAMERA_INFO_NAME) << " serial=" << serial
                << " camera_firmware=" << info(api, device.get(), RS2_CAMERA_INFO_FIRMWARE_VERSION)
                << " USB=" << info(api, device.get(), RS2_CAMERA_INFO_USB_TYPE_DESCRIPTOR) << '\n';
      if((!options.serial.empty() && options.serial == serial) || (options.serial.empty() && count == 1))
      {
        target = enumerate(api, device.get(), options.allProfiles);
        selectedSerial = serial;
        selected = std::move(device);
      }
    }
    if(!selected)
      throw std::runtime_error(count > 1 && options.serial.empty() ? "Multiple cameras: specify --serial; no camera was opened" : "No selected RealSense camera found");
    std::cout << "TARGET_SUPPORT color640x480_YUYV30=" << target.color << " depth424x240_Z16_5=" << target.depth << '\n';
    if(!target.color || (!options.colorOnly && !target.depth)) throw std::runtime_error("Required target profile absent; no streaming started");
    Pipeline pipeline(context.get(), api);
    Handle<rs2_config, rs2_delete_config> config(rs2_create_config(&api.error), rs2_delete_config);
    api.check("create stream config");
    rs2_config_disable_all_streams(config.get(), &api.error); api.check("clear default streams");
    rs2_config_enable_device(config.get(), selectedSerial.c_str(), &api.error); api.check("select serial");
    rs2_config_enable_stream(config.get(), RS2_STREAM_COLOR, 0, 640, 480, RS2_FORMAT_YUYV, 30, &api.error); api.check("configure color target");
    if(!options.colorOnly) { rs2_config_enable_stream(config.get(), RS2_STREAM_DEPTH, 0, 424, 240, RS2_FORMAT_Z16, 5, &api.error); api.check("configure depth keepalive target"); }
    const bool resolve = rs2_config_can_resolve(config.get(), pipeline.pipe, &api.error) != 0; api.check("resolve combined profile without streaming");
    std::cout << "CONFIG_CAN_RESOLVE=" << resolve << " (not proof that camera is free)\n";
    if(!resolve) throw std::runtime_error("Combined profile cannot resolve; no streaming started");
    if(options.capture) capture(api, pipeline, config.get(), options);
    else std::cout << "PASSIVE_OK stream_started=false options_changed=false\n";
    return EXIT_SUCCESS;
  }
  catch(const std::exception& error)
  {
    std::cerr << "D455_DIAGNOSTIC_FAILED: " << error.what() << "\n"
                 "If USB is busy, report the owner; this tool never stops/kills other programs or changes robot mode.\n";
    return EXIT_FAILURE;
  }
}
