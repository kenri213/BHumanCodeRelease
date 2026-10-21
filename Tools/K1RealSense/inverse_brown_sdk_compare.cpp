// Pure SDK geometry comparison; no context/device/pipeline, camera stream or actuators.
#include "../../Src/Modules/Infrastructure/CameraProvider/RealSenseCameraGeometry.h"
#include <librealsense2/rs.h>
#include <librealsense2/rsutil.h>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <iostream>

int main()
{
  try
  {
    const RealSenseCamera::Intrinsics k{640,480,386.291962f,385.947784f,329.906769f,241.425186f};
    const std::array<float,5> c{-.049901f,.055968f,-.001792f,-.000093f,-.016668f};
    rs2_intrinsics i{};
    i.width=k.width; i.height=k.height; i.fx=k.fx; i.fy=k.fy; i.ppx=k.cx; i.ppy=k.cy;
    i.model=RS2_DISTORTION_INVERSE_BROWN_CONRADY;
    std::copy(c.begin(),c.end(),i.coeffs);
    const auto map=RealSenseCamera::inverseBrownMap(k,c);
    float projectionError=0.f, roundtripError=0.f;
    for(int v=0;v<k.height;++v)
      for(int u=0;u<k.width;++u)
      {
        const auto& p=map[static_cast<std::size_t>(v)*k.width+u];
        const float ray[3]{(u-k.cx)/k.fx,(v-k.cy)/k.fy,1.f};
        float pixel[2];
        rs2_project_point_to_pixel(pixel,&i,ray);
        if(!std::isfinite(pixel[0]) || !std::isfinite(pixel[1])) return EXIT_FAILURE;
        projectionError=std::max({projectionError,std::abs(pixel[0]-p.x),std::abs(pixel[1]-p.y)});
        const float raw[2]{p.x,p.y};
        float deprojected[3];
        rs2_deproject_pixel_to_point(deprojected,&i,raw,1.f);
        if(!std::isfinite(deprojected[0]) || !std::isfinite(deprojected[1])) return EXIT_FAILURE;
        roundtripError=std::max({roundtripError,std::abs(deprojected[0]*k.fx+k.cx-u),std::abs(deprojected[1]*k.fy+k.cy-v)});
      }
    std::cout << std::setprecision(9) << "SDK_INVERSE_BROWN_FULLGRID pixels=" << map.size()
              << " project_max_error_px=" << projectionError << " deproject_roundtrip_max_error_px=" << roundtripError
              << " no_context=true no_stream=true no_actuators=true\n";
    if(projectionError > .002f || roundtripError > .002f)
    {
      std::cerr << "FAILED: mapping disagrees with actual linked librealsense geometry\n";
      return EXIT_FAILURE;
    }
    std::cout << "SDK_COMPARE_PASS\n";
    return EXIT_SUCCESS;
  }
  catch(const std::exception& error)
  {
    std::cerr << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
