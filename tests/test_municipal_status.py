"""Read-only policy status and dispatch probes; fictional data, no fiscal network."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import fiscal_safety as safety
import municipal_runtime as runtime
from emitir_nfse_municipal import emitir_nota

class FiscalStatusTests(unittest.TestCase):
 def setUp(self):
  self.directory=tempfile.TemporaryDirectory();self.root=Path(self.directory.name)
  self.config=json.loads((BASE/'config-preview.exemplo.json').read_text(encoding='utf-8'))
  self.config['layout2_municipal']['envio_habilitado']=True
  self.config_file=self.root/'config.json';self.config_file.write_text(json.dumps(self.config),encoding='utf-8')
  self.counter=self.root/'contador_rps.txt';self.counter.write_text('45\n')
 def tearDown(self):self.directory.cleanup()
 def test_status_reads_policy_and_counter_without_credentials_or_network(self):
  with patch.object(safety,'BASE_DIR',self.root),patch.object(runtime,'validate_certificate',side_effect=AssertionError('credential access')),patch.object(runtime.requests.Session,'post',side_effect=AssertionError('network')):
   status=safety.fiscal_status(str(self.config_file))
  self.assertTrue(status['producao_habilitada']);self.assertFalse(status['trava_hardcoded_emissao_layout2'])
  self.assertEqual(status['proximo_rps'],45);self.assertFalse(status['emissao_realizada']);self.assertFalse(status['rede_utilizada'])
  self.assertEqual(self.counter.read_text(),'45\n')
 def test_status_reflects_configuration_disabled(self):
  self.config['layout2_municipal']['envio_habilitado']=False;self.config_file.write_text(json.dumps(self.config),encoding='utf-8')
  with patch.object(safety,'BASE_DIR',self.root):result=safety.fiscal_status(str(self.config_file))
  self.assertFalse(result['producao_habilitada'])
 def test_status_cli_does_not_require_payload_and_works_outside_repository(self):
  command=[sys.executable,str(BASE/'emitir_nfse_municipal.py'),'--status','--config',str(self.config_file)]
  run=subprocess.run(command,cwd=self.root,capture_output=True,encoding='utf-8');self.assertEqual(run.returncode,0,run.stderr)
  status=json.loads(run.stdout);self.assertEqual(status['tipo'],'status_fiscal_local');self.assertFalse(status['emissao_realizada']);self.assertFalse(status['rede_utilizada'])
 def test_real_dispatch_without_operation_id_is_not_maintenance_lock(self):
  data=json.loads((BASE/'payload-layout2-municipal.exemplo.json').read_text(encoding='utf-8'));data['documento_tomador']='11222333000181'
  data.pop('id_operacao',None);path=self.root/'data.json';path.write_text(json.dumps(data),encoding='utf-8')
  with patch.object(runtime,'MunicipalClient',side_effect=AssertionError('credential access')),patch.object(runtime.requests.Session,'post',side_effect=AssertionError('network')):
   result=emitir_nota(str(self.config_file),str(path),'producao',2)
  self.assertIn(result['erros'][0]['codigo'],['OPERATION_ID','WSL_REQUIRED']);self.assertFalse(result['emissao_realizada'])

if __name__=='__main__':unittest.main()
