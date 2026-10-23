/** Prepare the fixed 987 log directory before starting any body control. */
#pragma once

#include <cerrno>
#include <cstring>
#include <stdexcept>
#include <string>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace BoosterMatchLogDirectory
{
  namespace detail
  {
    class Fd
    {
    public:
      explicit Fd(int value) : value(value) {}
      ~Fd() {if(value >= 0) ::close(value);}
      Fd(const Fd&) = delete;
      Fd& operator=(const Fd&) = delete;
      int get() const {return value;}
    private:
      int value;
    };

    [[noreturn]] inline void fail(const char* operation)
    {
      const int error = errno;
      throw std::runtime_error(std::string("987 log directory: ") + operation + ": " + std::strerror(error));
    }

    inline void verifyDirectory(int fd, uid_t owner, gid_t group)
    {
      struct stat info{};
      if(fd < 0 || ::fstat(fd, &info) != 0) fail("open/stat directory");
      // Existing owner-private or same-group writable log directories are fine.
      // Do not chmod an existing directory or apply vendor-file mode rules here.
      if(!S_ISDIR(info.st_mode) || info.st_uid != owner || (info.st_mode & S_IWOTH)
         || ((info.st_mode & S_IWGRP) && info.st_gid != group))
        throw std::runtime_error("987 log directory: unexpected owner/type or untrusted writable directory");
    }

    inline int openDirectoryAt(int parent, const char* name, uid_t owner, gid_t group)
    {
      const int fd = ::openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
      if(fd < 0) fail("open directory without symlinks");
      try {verifyDirectory(fd, owner, group);}
      catch(...) {::close(fd); throw;}
      return fd;
    }

    /** Uses an already verified home fd; this helper has no SDK/service calls. */
    inline bool ensureLogsAtHome(int homeFd, uid_t owner, gid_t group)
    {
      verifyDirectory(homeFd, owner, group);
      bool created = false;
      if(::mkdirat(homeFd, "logs", 0750) == 0) created = true;
      else if(errno != EEXIST) fail("create logs");

      Fd logs(openDirectoryAt(homeFd, "logs", owner, group));
      const std::string probe = ".bhuman_write_probe_" + std::to_string(::getpid());
      const int probeFd = ::openat(logs.get(), probe.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
      if(probeFd < 0) fail("verify log directory writable");
      // The empty, exclusively created probe is the only file ever removed.
      ::close(probeFd);
      if(::unlinkat(logs.get(), probe.c_str(), 0) != 0) fail("remove empty write probe");
      return created;
    }
  }

  /** Called only after the Main 987 identity and exact project path checks.
   * It creates no log file itself and never claims that Logger is recording.
   */
  inline bool prepare987()
  {
    if(::geteuid() != 1000 || ::getegid() != 1000)
      throw std::runtime_error("987 log directory: match APP must run as booster uid/gid 1000");
    detail::Fd root(::open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
    detail::verifyDirectory(root.get(), 0, 0);
    detail::Fd home(detail::openDirectoryAt(root.get(), "home", 0, 0));
    detail::Fd booster(detail::openDirectoryAt(home.get(), "booster", 1000, 1000));
    return detail::ensureLogsAtHome(booster.get(), 1000, 1000);
  }
}
