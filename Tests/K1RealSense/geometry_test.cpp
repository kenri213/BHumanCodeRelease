#include "../../Src/Modules/Infrastructure/CameraProvider/RealSenseCameraGeometry.h"
#include <array>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

namespace
{
  unsigned checks = 0;
  void check(bool value, const std::string& name)
  {
    ++checks;
    if(!value)
      throw std::runtime_error("FAILED: " + name);
  }
  void near(float value, float expected, const std::string& name, float tolerance = 1e-5f)
  {
    check(std::isfinite(value) && std::abs(value - expected) <= tolerance, name);
  }
  template<typename F> void invalid(F action, const std::string& name)
  {
    bool caught = false;
    try { action(); }
    catch(const std::invalid_argument&) { caught = true; }
    check(caught, name);
  }
  std::vector<RealSenseCamera::MapPoint> identity(int width, int height)
  {
    std::vector<RealSenseCamera::MapPoint> result;
    for(int y = 0; y < height; ++y)
      for(int x = 0; x < width; ++x)
        result.push_back({static_cast<float>(x), static_cast<float>(y)});
    return result;
  }
  void intrinsics()
  {
    using RealSenseCamera::Intrinsics;
    const Intrinsics good{640, 480, 400.f, 500.f, 311.f, 237.f};
    check(good.valid(), "asymmetric intrinsics");
    near(good.openingAngleWidth(), 2.f * std::atan(.8f), "fx horizontal FOV");
    near(good.openingAngleHeight(), 2.f * std::atan(.48f), "fy vertical FOV");
    near(640.f / (2.f * std::tan(good.openingAngleWidth() / 2.f)), 400.f, "fx round trip", .001f);
    near(480.f / (2.f * std::tan(good.openingAngleHeight() / 2.f)), 500.f, "fy round trip", .001f);
    for(int width : {-2, 0, 1, 639, 2562})
    {
      auto value = good; value.width = width;
      check(!value.valid(), "invalid width");
    }
    for(int height : {-1, 0, 1921})
    {
      auto value = good; value.height = height;
      check(!value.valid(), "invalid height");
    }
    const float nan = std::numeric_limits<float>::quiet_NaN();
    const float inf = std::numeric_limits<float>::infinity();
    for(float focal : {0.f, -1.f, nan, inf, -inf})
    {
      auto value = good; value.fx = focal; check(!value.valid(), "invalid fx");
      value = good; value.fy = focal; check(!value.valid(), "invalid fy");
    }
    for(float cx : {-1.f, 640.f, nan, inf})
    {
      auto value = good; value.cx = cx; check(!value.valid(), "invalid cx");
    }
    for(float cy : {-1.f, 480.f, nan, inf})
    {
      auto value = good; value.cy = cy; check(!value.valid(), "invalid cy");
    }
    check(Intrinsics{2, 1, 1.f, 1.f, 0.f, 0.f}.valid(), "minimum even image");
  }
  void timestamps()
  {
    using namespace RealSenseCamera;
    constexpr std::uint64_t base = 1780000000000ULL;
    auto result = timestamp(true, base + 100080, base, 100100, 100060, 500);
    check(result.source == TimestampSource::metadata && result.value == 100080, "metadata normalized");
    result = timestamp(false, 0, base, 100100, 100060, 500);
    check(result.source == TimestampSource::hostArrival && result.value == 100100, "no metadata host fallback");
    result = timestamp(true, 17000, base, 100100, 100060, 500);
    check(result.source == TimestampSource::hostArrival && result.value == 100100, "hardware clock host fallback");
    result = timestamp(true, base + 99000, base, 100100, 98000, 500);
    check(result.source == TimestampSource::rejected && result.value == 0, "stale metadata rejected");
    result = timestamp(true, base + 99600, base, 100100, 99000, 500);
    check(result.source == TimestampSource::metadata && result.value == 99600, "age boundary accepted");
    result = timestamp(true, base + 100060, base, 100100, 100060, 500);
    check(result.source == TimestampSource::rejected && result.value == 0, "duplicate not last+1");
    result = timestamp(true, base + 100059, base, 100100, 100060, 500);
    check(result.source == TimestampSource::rejected, "reordered rejected");
    result = timestamp(true, base + 100101, base, 100100, 100060, 500);
    check(result.source == TimestampSource::hostArrival && result.value == 100100, "future host fallback");
    result = timestamp(false, 0, base, 100060, 100060, 500);
    check(result.source == TimestampSource::rejected && result.value == 0, "now==last rejected");
    result = timestamp(true, base + 100050, base, 100059, 100060, 500);
    check(result.source == TimestampSource::rejected, "now<last rejected");
    result = timestamp(false, 0, base, 0, 0, 500);
    check(result.source == TimestampSource::rejected && result.value == 0, "zero marker rejected");
    result = timestamp(true, base + 100100, base, 100100, 100060, 0);
    check(result.source == TimestampSource::metadata && result.value == 100100, "zero age exact current frame");
  }
  void brown()
  {
    using namespace RealSenseCamera;
    const Intrinsics camera{4, 3, 2.f, 4.f, 1.f, 1.f};
    const std::array<float, 5> zero{};
    const auto unit = brownMap(camera, zero);
    check(unit.size() == 12, "map dimensions");
    for(int y = 0; y < camera.height; ++y)
      for(int x = 0; x < camera.width; ++x)
      {
        const auto& point = unit[static_cast<std::size_t>(y) * camera.width + x];
        near(point.x, static_cast<float>(x), "zero distortion identity x");
        near(point.y, static_cast<float>(y), "zero distortion identity y");
      }
    const auto radial = brownMap(camera, {.1f, .02f, 0.f, 0.f, .03f});
    // Pixel(3,2): normalized x=1,y=.25,r2=1.0625. Independent Brown reference.
    constexpr float r2 = 1.0625f;
    constexpr float factor = 1.f + .1f*r2 + .02f*r2*r2 + .03f*r2*r2*r2;
    near(radial[11].x, 1.f + 2.f*factor, "k1 k2 k3 radial x");
    near(radial[11].y, 1.f + factor, "k1 k2 k3 radial y");
    near(radial[5].x, 1.f, "principal point invariant x");
    near(radial[5].y, 1.f, "principal point invariant y");
    const auto tangential = brownMap(camera, {0.f, 0.f, .03f, -.02f, 0.f});
    constexpr float xd = 1.f + 2.f*.03f*.25f - .02f*(r2 + 2.f);
    constexpr float yd = .25f + 2.f*-.02f*.25f + .03f*(r2 + .125f);
    near(tangential[11].x, xd*2.f + 1.f, "p1 p2 tangential x");
    near(tangential[11].y, yd*4.f + 1.f, "p1 p2 tangential y");
    auto broken = camera; broken.fx = 0.f;
    invalid([&] { static_cast<void>(brownMap(broken, zero)); }, "invalid focal map rejected");
    for(std::size_t n = 0; n < zero.size(); ++n)
    {
      auto coefficients = zero;
      coefficients[n] = std::numeric_limits<float>::quiet_NaN();
      invalid([&] { static_cast<void>(brownMap(camera, coefficients)); }, "NaN coefficient rejected");
      coefficients[n] = std::numeric_limits<float>::infinity();
      invalid([&] { static_cast<void>(brownMap(camera, coefficients)); }, "infinite coefficient rejected");
    }
  }
  void inverseBrown()
  {
    using namespace RealSenseCamera;
    const Intrinsics small{4, 3, 2.f, 4.f, 1.f, 1.f};
    const std::array<float, 5> zero{};
    const auto unit = inverseBrownMap(small, zero);
    bool exact = true;
    for(int y = 0; y < small.height; ++y)
      for(int x = 0; x < small.width; ++x)
      {
        const auto& p = unit[static_cast<std::size_t>(y)*small.width+x];
        exact &= p.x == x && p.y == y;
      }
    check(exact, "inverse Brown exact zero-distortion identity");
    const std::array<float, 5> coupled{.1f,.02f,.03f,-.02f,.03f};
    const auto mapped = inverseBrownMap(small, coupled);
    // SDK inverse projection: r2 from original (1,.25), tangential a,b from scaled ray.
    constexpr float r2 = 1.0625f;
    constexpr float f = 1.f + .1f*r2 + .02f*r2*r2 + .03f*r2*r2*r2;
    constexpr float a = f, b = .25f*f;
    constexpr float xd = a + 2.f*.03f*a*b - .02f*(r2+2.f*a*a);
    constexpr float yd = b + 2.f*-.02f*a*b + .03f*(r2+2.f*b*b);
    near(mapped[11].x, xd*2.f+1.f, "SDK inverse coupled radial+tangential x");
    near(mapped[11].y, yd*4.f+1.f, "SDK inverse coupled radial+tangential y");
    near(mapped[5].x, 1.f, "inverse principal point invariant x");
    near(mapped[5].y, 1.f, "inverse principal point invariant y");
    const auto standard = brownMap(small, coupled);
    check(std::abs(mapped[11].x-standard[11].x) > .001f, "inverse not standard Brown x");
    check(std::abs(mapped[11].y-standard[11].y) > .001f, "inverse not standard Brown y");
    auto bad = small; bad.fy = 0.f;
    invalid([&] { static_cast<void>(inverseBrownMap(bad, zero)); }, "inverse invalid intrinsics");
    for(std::size_t n = 0; n < zero.size(); ++n)
    {
      auto c = zero; c[n] = std::numeric_limits<float>::quiet_NaN();
      invalid([&] { static_cast<void>(inverseBrownMap(small,c)); }, "inverse NaN coefficient");
      c[n] = std::numeric_limits<float>::infinity();
      invalid([&] { static_cast<void>(inverseBrownMap(small,c)); }, "inverse infinite coefficient");
    }
    invalid([&] { static_cast<void>(inverseBrownMap(small, {-1.f,0.f,0.f,0.f,0.f})); }, "inverse singular radial rejected");
    invalid([&] { static_cast<void>(inverseBrownMap(small, {std::numeric_limits<float>::max(),0.f,0.f,0.f,0.f})); }, "inverse nonfinite mapped pixel rejected");

    const Intrinsics d455{640,480,386.291962f,385.947784f,329.906769f,241.425186f};
    const std::array<float,5> c{-.049901f,.055968f,-.001792f,-.000093f,-.016668f};
    const auto map = inverseBrownMap(d455,c);
    check(map.size() == 640u*480u, "D455 inverse full map");
    // Independent double reference computed from SDK v2.55.1 source, not this helper.
    near(map[0].x, 2.1814276281f, "D455 top left x", .0002f);
    near(map[0].y, .8507486899f, "D455 top left y", .0002f);
    near(map[639].x, 636.4390373403f, "D455 top right x", .0002f);
    near(map[639].y, 1.2579241645f, "D455 top right y", .0002f);
    near(map[479u*640u].x, 3.6746019164f, "D455 bottom left x", .0002f);
    near(map[479u*640u].y, 475.5586320038f, "D455 bottom left y", .0002f);
    near(map.back().x, 635.0456949918f, "D455 bottom right x", .0002f);
    near(map.back().y, 475.2839169705f, "D455 bottom right y", .0002f);
    double worst = 0;
    bool allFinite = true;
    // Independent 10-step SDK deproject reference, double precision.
    for(int v = 0; v < d455.height; ++v)
      for(int u = 0; u < d455.width; ++u)
      {
        const auto& p = map[static_cast<std::size_t>(v)*d455.width+u];
        const double xo = (static_cast<double>(p.x)-d455.cx)/d455.fx;
        const double yo = (static_cast<double>(p.y)-d455.cy)/d455.fy;
        double x = xo, y = yo;
        for(int iteration=0; iteration<10; ++iteration)
        {
          const double r = x*x+y*y;
          const double ic = 1./(1.+c[0]*r+c[1]*r*r+c[4]*r*r*r);
          const double xq=x/ic, yq=y/ic;
          const double dx=2.*c[2]*xq*yq+c[3]*(r+2.*xq*xq);
          const double dy=2.*c[3]*xq*yq+c[2]*(r+2.*yq*yq);
          x=(xo-dx)*ic; y=(yo-dy)*ic;
        }
        allFinite &= std::isfinite(x) && std::isfinite(y);
        worst=std::max({worst,std::abs(x*d455.fx+d455.cx-u),std::abs(y*d455.fy+d455.cy-v)});
      }
    check(allFinite && worst < .002, "D455 full grid 10-step inverse deproject roundtrip <.002px");
    std::cout << "D455 inverse roundtrip worst_px=" << worst << '\n';
  }

  void yuyv()
  {
    using namespace RealSenseCamera;
    constexpr int width = 4, height = 2;
    const std::array<std::uint8_t, 16> source{{10,20,30,40,50,60,70,80,90,100,110,120,130,140,150,160}};
    std::array<std::uint8_t, 16> output{};
    const auto map = identity(width, height);
    rectifyYuyv(source.data(), width, height, 8, map, output.data());
    check(source == output, "identity every YUYV byte");
    const std::array<std::uint8_t, 24> padded{{10,20,30,40,50,60,70,80,255,254,253,252,
                                            90,100,110,120,130,140,150,160,251,250,249,248}};
    rectifyYuyv(padded.data(), width, height, 12, map, output.data());
    check(source == output, "padding ignored");
    auto swapped = map;
    for(int y = 0; y < height; ++y)
      for(int x = 0; x < width; ++x)
        swapped[static_cast<std::size_t>(y)*width+x].x = static_cast<float>((x+2)%width);
    rectifyYuyv(source.data(), width, height, 8, swapped, output.data());
    const std::array<std::uint8_t, 16> expected{{50,60,70,80,10,20,30,40,130,140,150,160,90,100,110,120}};
    check(output == expected, "pair UV order retained");
    auto halfX = map; halfX[0] = {.5f,0.f}; halfX[1] = {1.5f,0.f};
    rectifyYuyv(source.data(), width, height, 8, halfX, output.data());
    check(output[0] == 20 && output[2] == 40, "horizontal luma interpolation");
    check(output[1] == 30 && output[3] == 50, "shared pair centre chroma interpolation");
    auto halfY = map; halfY[0].y = .5f; halfY[1].y = .5f;
    rectifyYuyv(source.data(), width, height, 8, halfY, output.data());
    check(output[0] == 50 && output[2] == 70 && output[1] == 60 && output[3] == 80, "vertical luma chroma interpolation");
    auto outside = map; outside[0] = {-1.f,0.f};
    rectifyYuyv(source.data(), width, height, 8, outside, output.data());
    check(output[0] == 0 && output[2] == 30 && output[1] == 128 && output[3] == 128, "one invalid point neutral pair chroma");
    outside[1] = {static_cast<float>(width),static_cast<float>(height)};
    rectifyYuyv(source.data(), width, height, 8, outside, output.data());
    check(output[0] == 0 && output[2] == 0 && output[1] == 128 && output[3] == 128, "invalid whole pair black neutral");
    outside = map; outside[0].x = std::numeric_limits<float>::quiet_NaN();
    outside[1].y = std::numeric_limits<float>::infinity();
    rectifyYuyv(source.data(), width, height, 8, outside, output.data());
    check(output[0] == 0 && output[2] == 0 && output[1] == 128 && output[3] == 128, "nonfinite map never source-indexed");
    const std::array<std::uint8_t, 16> neutral{{10,128,30,128,50,128,70,128,90,128,110,128,130,128,150,128}};
    rectifyYuyv(neutral.data(), width, height, 8, halfX, output.data());
    for(std::size_t n = 1; n < output.size(); n += 2)
      check(output[n] == 128, "neutral stays neutral");
    const std::array<std::uint8_t, 4> smallest{{16,128,235,128}};
    std::array<std::uint8_t, 4> smallOutput{};
    rectifyYuyv(smallest.data(), 2, 1, 4, identity(2,1), smallOutput.data());
    check(smallest == smallOutput, "minimum image identity");
    invalid([&] { rectifyYuyv(nullptr,width,height,8,map,output.data()); }, "null source");
    invalid([&] { rectifyYuyv(source.data(),width,height,8,map,nullptr); }, "null output");
    for(int w : {-2,0,3})
      invalid([&] { rectifyYuyv(source.data(),w,height,8,map,output.data()); }, "invalid width");
    for(int h : {-1,0})
      invalid([&] { rectifyYuyv(source.data(),width,h,8,map,output.data()); }, "invalid height");
    for(int stride : {-1,7})
      invalid([&] { rectifyYuyv(source.data(),width,height,stride,map,output.data()); }, "short stride");
    auto shortMap = map; shortMap.pop_back();
    invalid([&] { rectifyYuyv(source.data(),width,height,8,shortMap,output.data()); }, "short map");
    invalid([&] { rectifyYuyv(source.data(),width,height,8,{},output.data()); }, "empty map");
  }
}

int main()
{
  try
  {
    intrinsics(); timestamps(); brown(); inverseBrown(); yuyv();
    std::cout << "K1 RealSense geometry: " << checks << " checks passed\n";
    return EXIT_SUCCESS;
  }
  catch(const std::exception& error)
  {
    std::cerr << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
