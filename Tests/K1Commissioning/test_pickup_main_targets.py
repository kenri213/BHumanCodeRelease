"""Offline checks for the two explicitly hardware-bound match entries."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / 'Src/Apps/Booster/Main.cpp'
BASE = '35b4d3cee3e8f3d8e8774a01f73cefde44ee0fd4'


def body(text, marker):
    begin = text.index('{', text.index(marker))
    depth = 1
    end = begin + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[begin + 1:end - 1]


class PickupMainTargets(unittest.TestCase):
    def test_original_996_live_scope_is_byte_preserved(self):
        before = subprocess.check_output(['git', 'show', BASE + ':Src/Apps/Booster/Main.cpp'], cwd=ROOT, text=True)
        self.assertEqual(body(before, 'if(officialLiveTest)\n    {'),
                         body(MAIN.read_text(), 'else if(officialLiveTest)\n    {'))

    def test_987_identity_helper_rejects_foreign_hardware_and_directory(self):
        text = MAIN.read_text()
        signature = 'static bool match987IdentityAllowed(const std::string& actualSerial, const char* bhDir)'
        helper = signature + '{' + body(text, signature) + '}'
        program = '#include <string>\n#include <cstring>\n' + helper + r'''
int main() {
  const char* own = "/home/booster/Workspace/BHuman_987_Match_20261023";
  if(!match987IdentityAllowed("1424725033885", own)) return 1;
  if(match987IdentityAllowed("1424725032007", own)) return 2;
  if(match987IdentityAllowed("1424725033885", "/home/booster/Workspace/BHuman_996_Match_20261019")) return 3;
  if(match987IdentityAllowed("1424725033885", nullptr)) return 4;
  if(match987IdentityAllowed("1424725033885", "")) return 5;
  if(match987IdentityAllowed("1424725033885", "/tmp/other")) return 6;
  return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='pickup-main-targets-') as directory:
            root = Path(directory).resolve()
            source, executable = root / 'main.cpp', root / 'test'
            source.write_text(program)
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_987_entry_checks_identity_before_enabling_control(self):
        text = MAIN.read_text()
        marker = 'if(officialMatch987)\n    {'
        scope = body(text, marker)
        self.assertLess(scope.index('DENIED_MIXED_SCOPE'), scope.index('match987IdentityAllowed'))
        self.assertLess(scope.index('match987IdentityAllowed'), scope.index('BoosterOfficialLiveTest::enable()'))
        self.assertNotIn('BoosterAppCommissioning::enable', scope)

    def test_target_does_not_reintroduce_match_runtime_deadline(self):
        scope = body(MAIN.read_text(), 'if(officialMatch987)\n    {')
        self.assertNotRegex(scope, r'setDuration|startDeadline|appSeconds\s*=')
        self.assertIn('NO_FIXED_RUNTIME_LIMIT', scope)


if __name__ == '__main__':
    unittest.main()
