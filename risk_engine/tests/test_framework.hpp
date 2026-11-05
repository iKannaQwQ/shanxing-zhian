#pragma once
// 极简测试框架：零外部依赖，保证 Windows 上无需联网即可构建。
// 用法：
//   TEST(name) { CHECK(expr); CHECK_EQ_STR(a, b); }
//   int main() { return tf::run_all(); }

#include <exception>
#include <functional>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace tf {

struct TestCase {
  std::string name;
  std::function<void()> fn;
};

inline std::vector<TestCase>& registry() {
  static std::vector<TestCase> r;
  return r;
}

inline int& failure_count() {
  static int n = 0;
  return n;
}

inline std::string& current_test() {
  static std::string s;
  return s;
}

struct Registrar {
  Registrar(std::string name, std::function<void()> fn) {
    registry().push_back(TestCase{std::move(name), std::move(fn)});
  }
};

inline void report_failure(const char* file, int line, const std::string& message) {
  ++failure_count();
  std::cerr << "  FAIL [" << current_test() << "] " << file << ":" << line << "  " << message
            << "\n";
}

inline int run_all() {
  int failed_tests = 0;
  for (auto& tc : registry()) {
    current_test() = tc.name;
    const int before = failure_count();
    try {
      tc.fn();
    } catch (const std::exception& e) {
      report_failure(__FILE__, __LINE__, std::string("未捕获异常: ") + e.what());
    } catch (...) {
      report_failure(__FILE__, __LINE__, "未捕获的未知异常");
    }
    if (failure_count() > before) ++failed_tests;
  }
  std::cout << (registry().size() - failed_tests) << "/" << registry().size() << " 用例通过";
  if (failure_count() > 0) {
    std::cout << "（断言失败 " << failure_count() << " 处）";
  }
  std::cout << "\n";
  return failure_count() == 0 ? 0 : 1;
}

}  // namespace tf

#define TEST(name)                                            \
  static void name();                                         \
  static ::tf::Registrar reg_##name(#name, name);             \
  static void name()

#define CHECK(cond)                                                       \
  do {                                                                    \
    if (!(cond)) ::tf::report_failure(__FILE__, __LINE__, #cond);         \
  } while (0)

#define CHECK_EQ_STR(actual, expected)                                          \
  do {                                                                          \
    const std::string a_ = (actual);                                            \
    const std::string e_ = (expected);                                          \
    if (a_ != e_) {                                                             \
      ::tf::report_failure(__FILE__, __LINE__,                                  \
                           std::string(#actual " = \"") + a_ + "\" != \"" + e_ + "\""); \
    }                                                                           \
  } while (0)

#define CHECK_NEAR(actual, expected, tol)                                        \
  do {                                                                           \
    const double a_ = (actual);                                                  \
    const double e_ = (expected);                                                \
    if (!((a_) > (e_) - (tol) && (a_) < (e_) + (tol))) {                         \
      std::ostringstream os_;                                                    \
      os_ << #actual " = " << a_ << " 不在 " << e_ << "±" << tol << " 内";       \
      ::tf::report_failure(__FILE__, __LINE__, os_.str());                       \
    }                                                                            \
  } while (0)
