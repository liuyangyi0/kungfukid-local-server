import hashlib
import unittest
from unittest.mock import patch
from server.kk_local import stage_catalog as stage


def fixture():
    #Synthetic source grammar, not an original-client resource payload.
    names=('z','a','b','c','d')
    pieces=['tMapMonsters={']+[f'["{n}"]={{"{n}",1}},' for n in names]+['\n};']
    for side in range(1,5):
        pieces.append(f'tMonsterBorn1_{side} = {{ MonsterList={{"z","a","z","b"}},\n}};')
    pieces.append('function MonsterWaveBegin(WaveIndex)\nif WaveIndex>=1 and WaveIndex<=25 then')
    for side in range(1,5):pieces.append(f'tMB{side} = tMonsterBorn1_{side};tMB{side}.BornConfig.Count = 2 + WaveIndex - 1;')
    pieces.append('end\nlocal tWave')
    return '\n'.join(pieces).encode(),b'fixture-runtime'


class StageCatalogTests(unittest.TestCase):
    def test_strict_version_and_full_template_sort_and_nil_count_clamp(self):
        script,runtime=fixture()
        with patch.object(stage,'SCRIPT_SHA',hashlib.sha256(script).hexdigest()),patch.object(stage,'RUNTIME_SHA',hashlib.sha256(runtime).hexdigest()):
            plan=stage.compile_zombie(script,runtime)
            self.assertEqual(plan.templates,('a','b','c','d','z'))
            self.assertEqual(plan.waves(1)[0],{0:2,4:2})
            self.assertEqual(plan.waves(1)[24],{0:2,1:2,4:4})
            self.assertEqual(sum(plan.waves(3)[24].values()),12)
            self.assertEqual(sum(plan.waves(5)[24].values()),16)
            self.assertEqual(len(plan.waves(2)),25)
            with self.assertRaises(ValueError):plan.waves(9)
            with self.assertRaises(ValueError):stage.compile_zombie(script+b' ',runtime)
            with self.assertRaises(ValueError):stage.compile_zombie(script,runtime+b' ')

    def test_unknown_script_is_never_executed_or_assigned_a_fallback(self):
        with self.assertRaises(ValueError):stage.compile_zombie(b'os.execute("bad")',b'anything')
