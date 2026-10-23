"""Real fd/openat tests for the exact log-directory helper; no robot or SDK."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

SOURCE = r'''
#include "Platform/BoosterMatchLogDirectory.h"
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <string>
namespace fs=std::filesystem;
namespace L=BoosterMatchLogDirectory;
static void check(bool value){if(!value)std::abort();}
int main(int argc,char**argv){
  check(argc==3);const std::string name=argv[1];const fs::path home=argv[2];
  const auto uid=geteuid();const auto gid=getegid();
  L::detail::Fd fd(open(home.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
  if(name=="new"){
    check(L::detail::ensureLogsAtHome(fd.get(),uid,gid));
    check(fs::is_directory(home/"logs"));
    check(!L::detail::ensureLogsAtHome(fd.get(),uid,gid));
    check(fs::is_empty(home/"logs"));
  }else if(name=="existing"){
    fs::create_directory(home/"logs");chmod((home/"logs").c_str(),0770);
    FILE*f=fopen((home/"logs"/"preserve.log").c_str(),"wx");check(f);fputs("evidence",f);fclose(f);
    check(!L::detail::ensureLogsAtHome(fd.get(),uid,gid));
    struct stat info{};check(stat((home/"logs").c_str(),&info)==0&&(info.st_mode&0777)==0770);
    check(fs::file_size(home/"logs"/"preserve.log")==8);
  }else if(name=="symlink"){
    fs::create_directory(home/"other");fs::create_directory_symlink(home/"other",home/"logs");
    try{L::detail::ensureLogsAtHome(fd.get(),uid,gid);return 2;}catch(const std::runtime_error&){}
    check(fs::is_empty(home/"other"));
  }else if(name=="file"){
    FILE*f=fopen((home/"logs").c_str(),"wx");check(f);fclose(f);
    try{L::detail::ensureLogsAtHome(fd.get(),uid,gid);return 2;}catch(const std::runtime_error&){}
  }else if(name=="world_writable"){
    fs::create_directory(home/"logs");chmod((home/"logs").c_str(),0777);
    try{L::detail::ensureLogsAtHome(fd.get(),uid,gid);return 2;}catch(const std::runtime_error&){}
    struct stat info{};check(stat((home/"logs").c_str(),&info)==0&&(info.st_mode&0777)==0777);
  }else if(name=="owner"){
    try{L::detail::ensureLogsAtHome(fd.get(),uid+1,gid);return 2;}catch(const std::runtime_error&){}
    check(!fs::exists(home/"logs"));
  }else if(name=="probe_collision"){
    fs::create_directory(home/"logs");
    auto probe=home/"logs"/(".bhuman_write_probe_"+std::to_string(getpid()));
    FILE*f=fopen(probe.c_str(),"wx");check(f);fputs("keep",f);fclose(f);
    try{L::detail::ensureLogsAtHome(fd.get(),uid,gid);return 2;}catch(const std::runtime_error&){}
    check(fs::file_size(probe)==4);
  }else return 3;
}
'''


class MatchLogDirectoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="bhuman-log-helper-")
        cls.root = Path(cls.directory.name).resolve()
        source = cls.root / "helper.cpp"
        cls.binary = cls.root / "helper"
        source.write_text(SOURCE)
        result = subprocess.run([shutil.which("c++") or "c++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
                                 "-I", str(ROOT / "Src/Libs"), str(source), "-o", str(cls.binary)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def replay(self, name):
        with tempfile.TemporaryDirectory(prefix="home-", dir=self.root) as directory:
            result = subprocess.run([str(self.binary), name, directory], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


for _name in ("new", "existing", "symlink", "file", "world_writable", "owner", "probe_collision"):
    setattr(MatchLogDirectoryTest, "test_" + _name, lambda self, name=_name: self.replay(name))

if __name__ == "__main__":
    unittest.main()
