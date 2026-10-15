import copy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal
from lxml import etree
P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P))
import municipal_runtime as m
import fiscal_safety as safety
from emitir_nfse_municipal import emitir_nota
C=json.loads((P/'config-preview.exemplo.json').read_text(encoding='utf-8'))
D=json.loads((P/'payload-layout2-municipal.exemplo.json').read_text(encoding='utf-8'))
D.update(numero_rps='14',id_operacao='FATURA-FICTICIA-LOCAL',documento_tomador='11222333000181')
def fictional_note():
 n=etree.Element('NFe')
 for path, values in [('ChaveNFe', [('InscricaoPrestador',C['inscricao_municipal']),('NumeroNFe','999'),('CodigoVerificacao','ABCD1234')]),
  ('ChaveRPS',[('InscricaoPrestador',C['inscricao_municipal']),('SerieRPS',C['serie_rps']),('NumeroRPS','14')]),
  ('CPFCNPJTomador',[('CNPJ',D['documento_tomador'])])]:
  node=etree.SubElement(n,path)
  for k,v in values:etree.SubElement(node,k).text=str(v)
 for k,v in [('ValorFinalCobrado',D['valor_final_cobrado']),('ValorDeducoes',D['valor_deducoes']),('ValorIPI',D['valor_ipi']),
  ('AliquotaServicos',C['aliquota_servicos']),('CodigoServico',str(int(C['codigo_servico']))),('Discriminacao',D['discriminacao']),('ISSRetido','false'),('StatusNFe','N')]:etree.SubElement(n,k).text=str(v)
 for field,key in [('ValorPIS','pis'),('ValorCOFINS','cofins'),('ValorINSS','inss'),('ValorIR','ir'),('ValorCSLL','csll')]:etree.SubElement(n,field).text=D['retencoes'][key]
 settings=dict(C['campos_layout_2']);settings.update(D.get('ibscbs',{}));etree.SubElement(n,'NBS').text=settings['NBS']
 ibs=etree.SubElement(n,'IBSCBS')
 for k in ['finNFSe','indFinal','cIndOp','indDest']:etree.SubElement(ibs,k).text=str(settings[k])
 group=etree.SubElement(etree.SubElement(etree.SubElement(ibs,'valores'),'trib'),'gIBSCBS');etree.SubElement(group,'cClassTrib').text=settings['cClassTrib']
 return n
FIXTURE=fictional_note()

def note():
 n=copy.deepcopy(FIXTURE);n.find('ChaveRPS/NumeroRPS').text='14';return n

def soap(inner, method='EnvioLoteRPSResponse'):
 root=etree.Element('{'+m.SOAP+'}Envelope');b=etree.SubElement(root,'{'+m.SOAP+'}Body');r=etree.SubElement(b,'{'+m.NFE+'}'+method);etree.SubElement(r,'{'+m.NFE+'}RetornoXML').text=inner
 return etree.tostring(root)
def response(success=True,long_alert=False):
 root=etree.Element('{'+m.NFE+'}RetornoEnvioLoteRPS',nsmap={'nfe':m.NFE});h=etree.SubElement(root,'Cabecalho',Versao='2');etree.SubElement(h,'Sucesso').text=str(success).lower()
 if long_alert:
  a=etree.SubElement(root,'Alerta');etree.SubElement(a,'Codigo').text='1651';etree.SubElement(a,'Descricao').text='A'*400
 if success:
  n=etree.SubElement(root,'ChaveNFeRPS');n.append(copy.deepcopy(FIXTURE.find('ChaveNFe')));ch=copy.deepcopy(FIXTURE.find('ChaveRPS'));ch.find('NumeroRPS').text='14';n.append(ch)
 else:
  e=etree.SubElement(root,'Erro');etree.SubElement(e,'Codigo').text='123';etree.SubElement(e,'Descricao').text='Rejeitado'
 return type('Response',(),{'status_code':200,'content':soap(etree.tostring(root,encoding='unicode'))})()
class Fake:
 def __init__(self,queries=None,fail=False,bad=False):self.calls=0;self.queries=iter(queries if queries is not None else [None,note()]);self.fail=fail;self.bad=bad
 @contextmanager
 def connection(self):yield self
 def query(self,data,path):return next(self.queries)
 def emission(self,data,mode):return b'SIGNED-FIXTURE'
 def post(self,envelope,mode):
  self.calls+=1
  if self.fail:raise TimeoutError('simulated')
  return response(not self.bad,True)
@unittest.skipIf(m.fcntl is None, 'Production journal requires Linux/WSL POSIX locks')
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);(self.base/'contador_rps.txt').write_text('14\n')
  self.a=patch.object(m,'BASE',self.base);self.a.start();self.b=patch.object(m,'STATE',self.base/'state');self.b.start()
 def tearDown(self):self.b.stop();self.a.stop();self.temp.cleanup()
 def runprod(self,fake=None,data=None,digest='same'):return m.production(C,data or D,fake or Fake(),digest)
 def test_success_confirm_and_counter(self):
  f=Fake();r=self.runprod(f);self.assertTrue(r['confirmada_por_consulta']);self.assertEqual(f.calls,1);self.assertEqual((self.base/'contador_rps.txt').read_text().strip(),'15');self.assertEqual(len(r['alertas'][0]['descricao']),400)
 def test_repeat_returns_cached(self):
  self.runprod();f=Fake([]);r=self.runprod(f);self.assertTrue(r['reenvio_evitado']);self.assertEqual(f.calls,0)
 def test_changed_id_payload_rejected(self):
  self.runprod()
  with self.assertRaisesRegex(m.FiscalError,'outro payload'):self.runprod(Fake([]),digest='changed')
 def test_used_rps_not_sent(self):
  f=Fake([note()])
  with self.assertRaisesRegex(m.FiscalError,'já convertido'):self.runprod(f)
  self.assertEqual(f.calls,0)
 def test_timeout_persisted_no_resend(self):
  f=Fake(fail=True)
  with self.assertRaisesRegex(m.FiscalError,'Resultado incerto'):self.runprod(f)
  self.assertEqual((self.base/'contador_rps.txt').read_text().strip(),'14')
  second=Fake([None])
  with self.assertRaisesRegex(m.FiscalError,'Não reenviar'):self.runprod(second)
  self.assertEqual(second.calls,0)
 def test_timeout_recovery_consult_only(self):
  with self.assertRaises(m.FiscalError):self.runprod(Fake(fail=True))
  second=Fake([note()]);r=self.runprod(second);self.assertTrue(r['sucesso']);self.assertFalse(r['emissao_realizada_nesta_chamada']);self.assertEqual(second.calls,0)
 def test_mismatch_no_counter(self):
  n=note();n.find('ValorFinalCobrado').text='999'
  with self.assertRaises(m.FiscalError):self.runprod(Fake([None,n]))
  self.assertEqual((self.base/'contador_rps.txt').read_text().strip(),'14')
 def test_rejection_no_counter_and_no_resend(self):
  r=self.runprod(Fake(bad=True));self.assertFalse(r['sucesso']);self.assertEqual((self.base/'contador_rps.txt').read_text().strip(),'14')
  f=Fake([None])
  with self.assertRaises(m.FiscalError):self.runprod(f)
  self.assertEqual(f.calls,0)
 def test_counter_mismatch(self):
  d=dict(D,numero_rps='13');f=Fake()
  with self.assertRaisesRegex(m.FiscalError,'contador'):self.runprod(f,d)
  self.assertEqual(f.calls,0)
 def test_missing_operation_id(self):
  d=dict(D);d.pop('id_operacao')
  with self.assertRaises(m.FiscalError):self.runprod(Fake(),d)
 def test_concurrent_lock(self):
  m.STATE.mkdir();fd=m.os.open(m.STATE/'.lock',m.os.O_CREAT|m.os.O_RDWR,0o600);m.fcntl.flock(fd,m.fcntl.LOCK_EX)
  try:
   with self.assertRaisesRegex(m.FiscalError,'andamento'):self.runprod()
  finally:m.os.close(fd)
 def test_rps_reserved_by_other_id(self):
  with self.assertRaises(m.FiscalError):self.runprod(Fake(fail=True))
  with self.assertRaisesRegex(m.FiscalError,'reservado'):self.runprod(Fake([None]),dict(D,id_operacao='OTHER'))
 def test_malformed_and_http_not_success(self):
  for status,content in [(500,b''),(200,b'<bad/>')]:
   with self.assertRaises(m.FiscalError):m.response_inner(status,content,'RetornoEnvioLoteRPS')
 def test_fiscal_fields(self):
  for field,newval in [('NBS','000000000'),('IBSCBS/cIndOp','030105'),('CPFCNPJTomador/CNPJ','00000000000000'),('ValorIR','999')]:
   n=note();n.find(field).text=newval
   with self.assertRaises(m.FiscalError):m.confirm_note(n,C,D)
 def test_mode_mismatch_rejected_before_network(self):
  client=object.__new__(m.MunicipalClient)
  root=etree.Element('{'+m.SOAP+'}Envelope');body=etree.SubElement(root,'{'+m.SOAP+'}Body');req=etree.SubElement(body,'{'+m.NFE+'}EnvioLoteRPSRequest');etree.SubElement(req,'{'+m.NFE+'}VersaoSchema').text='2'
  with self.assertRaises(m.FiscalError):client.post(etree.tostring(root),'teste')
 def test_document_checks(self):
  self.assertTrue(m.valid_document('11222333000181'));self.assertFalse(m.valid_document('00000000000'));self.assertFalse(m.valid_document('11222333000182'))
 def test_series_mismatch(self):
  with self.assertRaisesRegex(m.FiscalError,'série'):m.execute(C,dict(D,serie_rps='OTHER'),'teste',{})
 def test_policy_disabled_before_cert(self):
  with patch.object(m,'MunicipalClient',side_effect=AssertionError('credentials touched')):
   disabled=copy.deepcopy(C);disabled['layout2_municipal']['envio_habilitado']=False
   with self.assertRaisesRegex(m.FiscalError,'desativada'):m.execute(disabled,D,'producao',{})
 def test_messages_blocked(self):
  with self.assertRaisesRegex(m.FiscalError,'e-mails'):m.execute(C,dict(D,email_tomador='test@example.com'),'teste',{})
 def test_dry_run_no_cert_or_network_both_layouts(self):
  data=self.base/'data.json';data.write_text(json.dumps(D))
  with patch.object(m,'validate_certificate',side_effect=AssertionError('credentials')),patch.object(m.requests.Session,'post',side_effect=AssertionError('network')):
   for layout in (1,2):
    for mode in ('teste','producao'):
     result=emitir_nota(str(P/'config-preview.exemplo.json'),str(data),mode,layout,dry_run=True);self.assertFalse(result['rede_utilizada']);self.assertFalse(result['assinatura_realizada'])
 def test_layout1_remains_blocked_in_new_entry(self):
  self.assertTrue(emitir_nota('missing','missing','producao',1)['bloqueado'])
 def test_no_payload_no_network(self):self.assertFalse(emitir_nota('missing',None,'producao')['rede_utilizada'])
 def test_test_mode_only_and_no_counter(self):
  c=copy.deepcopy(C)
  class TestFake(Fake):
   def emission(self,data,mode):assert mode=='teste';return b'TEST'
   def post(self,envelope,mode):
    assert mode=='teste'
    return type('Response',(),{'status_code':200,'content':soap('<nfe:RetornoEnvioLoteRPS xmlns:nfe="'+m.NFE+'"><Cabecalho Versao="2"><Sucesso>true</Sucesso></Cabecalho></nfe:RetornoEnvioLoteRPS>', method='TesteEnvioLoteRPSResponse')})()
  with patch.object(m,'MunicipalClient',return_value=TestFake()):result=m.execute(c,D,'teste',{})
  self.assertTrue(result['teste_aprovado']);self.assertFalse(result['emissao_realizada']);self.assertEqual((self.base/'contador_rps.txt').read_text().strip(),'14')
class ProductionProtocolTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  from cryptography.hazmat.primitives.asymmetric import rsa
  cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
 @classmethod
 def certificate(cls,expired=False,cnpj=None):
  from datetime import datetime,timedelta,timezone
  from cryptography import x509
  from cryptography.x509.oid import NameOID
  from cryptography.hazmat.primitives import hashes
  now=datetime.now(timezone.utc);name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,cnpj or C['cnpj_prestador'])])
  return x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(cls.key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=3)).not_valid_after(now+timedelta(days=-1 if expired else 1)).sign(cls.key,hashes.SHA256())
 def load_certificate(self,cert,key=None):
  with patch.object(m,'dotenv_values',return_value={'NFSE_CERT_PASSWORD':'fictional-test-password'}),patch.object(Path,'read_bytes',return_value=b'fixture'),patch.object(m.pkcs12,'load_key_and_certificates',return_value=(key or self.key,cert,None)):
   return m.validate_certificate(C)
 def test_ephemeral_certificate_matches_provider(self):
  key,cert=self.load_certificate(self.certificate());self.assertIs(key,self.key)
 def test_expired_certificate_stops_before_transport(self):
  with self.assertRaisesRegex(m.FiscalError,'validade'):self.load_certificate(self.certificate(expired=True))
 def test_wrong_certificate_provider_rejected(self):
  with self.assertRaisesRegex(m.FiscalError,'CNPJ'):self.load_certificate(self.certificate(cnpj='FICTIONAL OTHER PROVIDER'))
 def test_certificate_private_key_mismatch_rejected(self):
  from cryptography.hazmat.primitives.asymmetric import rsa
  wrong=rsa.generate_private_key(public_exponent=65537,key_size=2048)
  with self.assertRaisesRegex(m.FiscalError,'Chave privada'):self.load_certificate(self.certificate(),wrong)
 def test_production_explicitly_rejects_non_posix_before_credentials(self):
  c=copy.deepcopy(C);c['layout2_municipal']['envio_habilitado']=True
  with patch.object(m,'fcntl',None),patch.object(m,'MunicipalClient',side_effect=AssertionError('credential access')):
   with self.assertRaisesRegex(m.FiscalError,'Linux/WSL'):m.execute(c,D,'producao',{})
 def test_wrong_soap_method_rejected(self):
  content=response().content.replace(b'EnvioLoteRPSResponse',b'TesteEnvioLoteRPSResponse')
  with self.assertRaises(m.FiscalError):m.response_inner(200,content,'RetornoEnvioLoteRPS')
 def test_non_soap_wrapper_rejected(self):
  content=b'<Fake><RetornoXML>text</RetornoXML></Fake>'
  with self.assertRaises(m.FiscalError):m.response_inner(200,content,'RetornoEnvioLoteRPS')
 def test_duplicate_confirmation_rejected(self):
  text=response().content.replace(b'&lt;/Sucesso&gt;',b'&lt;/Sucesso&gt;&lt;Sucesso&gt;true&lt;/Sucesso&gt;')
  with self.assertRaises(m.FiscalError):m.response_inner(200,text,'RetornoEnvioLoteRPS')
 def test_namespaced_error_cannot_disappear(self):
  content=response().content.replace(b'&lt;/nfe:RetornoEnvioLoteRPS&gt;',b'&lt;nfe:Erro&gt;&lt;nfe:Codigo&gt;999&lt;/nfe:Codigo&gt;&lt;/nfe:Erro&gt;&lt;/nfe:RetornoEnvioLoteRPS&gt;')
  with self.assertRaises(m.FiscalError):m.response_inner(200,content,'RetornoEnvioLoteRPS')
 def test_missing_xml_rejected_before_network(self):
  root=etree.Element('{'+m.SOAP+'}Envelope');b=etree.SubElement(root,'{'+m.SOAP+'}Body');req=etree.SubElement(b,'{'+m.NFE+'}EnvioLoteRPSRequest');etree.SubElement(req,'{'+m.NFE+'}VersaoSchema').text='2'
  client=object.__new__(m.MunicipalClient)
  with self.assertRaises(m.FiscalError):client.post(etree.tostring(root),'producao')

if __name__=='__main__':unittest.main(verbosity=2)
