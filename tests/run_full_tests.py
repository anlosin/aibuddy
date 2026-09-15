# -*- coding: utf-8 -*-
"""全量回归 runner：Python 侧枚举 test_*.py（PowerShell 枚举偶发抖动），
排除长跑压测与手动探针，输出结果到 artifacts/ut_full3.txt。"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tests'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

sys.stdout.reconfigure(line_buffering=True)

TESTS = os.path.join(PROJ, 'tests')
names = []
for f in sorted(os.listdir(TESTS)):
    if f.startswith('test_') and f.endswith('.py') and f != 'test_java_stress.py':
        names.append('tests.' + f[:-3])
print(f'共 {len(names)} 个测试模块')

loader = unittest.TestLoader()
suite = loader.loadTestsFromNames(names)
runner = unittest.TextTestRunner(verbosity=1, stream=sys.stdout)
result = runner.run(suite)
print('RAN:', result.testsRun, 'FAILURES:', len(result.failures),
      'ERRORS:', len(result.errors))
sys.exit(0 if result.wasSuccessful() else 1)
