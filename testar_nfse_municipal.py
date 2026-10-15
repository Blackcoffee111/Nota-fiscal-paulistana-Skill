"""Exclusive TesteEnvioLoteRPS client. No production or cancellation route."""
import argparse
import base64
import json
import os
import sys
import tempfile
import warnings
from datetime import date
from decimal import Decimal
from pathlib import Path
import requests
from lxml import etree
from dotenv import dotenv_values
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12
from signxml import XMLSigner, XMLVerifier, SignatureConfiguration, SignatureMethod, DigestAlgorithm, SignatureConstructionMethod, CanonicalizationMethod
from fiscal_safety import _read_object, preview_emission
from municipal_layout2 import municipal_preview

BASE = Path(__file__).resolve().parent
TEST_ENDPOINT = 'https://nfews.prefeitura.sp.gov.br/lotenfe.asmx'
TEST_ACTION = '"http://www.prefeitura.sp.gov.br/nfe/ws/testeenvio"'
NFE = 'http://www.prefeitura.sp.gov.br/nfe'
SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'

class MunicipalSigner(XMLSigner):
    def check_deprecated_methods(self):
        # RSA-SHA1 is required by the municipal contract, manual 3.3.9.
        pass


def _digits(value, width):
    text = str(value)
    if not text.isascii() or not text.isdigit() or len(text)>width:
        raise ValueError('Campo numérico incompatível com cadeia de assinatura.')
    return text.zfill(width)


def signature_chain_v2(config, data):
    series = str(data.get('serie_rps',config.get('serie_rps','')))
    if not series.isascii() or not series or len(series)>5 or series.startswith(' '):
        raise ValueError('Série incompatível com assinatura municipal.')
    if 'valor_inicial_cobrado' in data or data.get('valor_final_cobrado') is None:
        raise ValueError('A assinatura deste cliente exige valor_final_cobrado explícito.')
    amount_keys=['valor_final_cobrado']
    document=''.join(c for c in str(data['documento_tomador']) if c.isalnum())
    indicator='1' if len(document)==11 else '2' if len(document)==14 else None
    if indicator is None or not document.isascii() or not document.isdigit():
        raise ValueError('Este teste implementa assinatura para CPF/CNPJ numérico, sem intermediário.')
    if data.get('intermediario'):raise ValueError('Intermediário requer implementação específica; não omitir.')
    def cents(value):
        number=Decimal(str(value))*100
        if not number.is_finite() or number<0 or number!=number.to_integral_value():raise ValueError('Valor monetário inválido para assinatura.')
        return _digits(int(number),15)
    taxation=str(data.get('tributacao_rps',config.get('tributacao_rps','T')))
    if len(taxation)!=1 or not taxation.isascii():raise ValueError('Tributação inválida.')
    chain=(_digits(config['inscricao_municipal'],12)+series.ljust(5)+_digits(data['numero_rps'],12)
           +date.fromisoformat(data['data_emissao']).strftime('%Y%m%d')+taxation+'N'
           +('S' if data['iss_retido'] else 'N')+cents(data[amount_keys[0]])+cents(data['valor_deducoes'])
           +_digits(data.get('codigo_servico',config['codigo_servico']),5)+indicator+_digits(document,14))
    if len(chain)!=90:raise ValueError('Cadeia municipal v2 sem intermediário deve ter 90 posições.')
    return chain


def build_signed_test(config, data, key, certificate):
    preview=municipal_preview(config,data)
    root=etree.fromstring(preview['xml_nao_assinado'].encode(),etree.XMLParser(no_network=True,resolve_entities=False))
    rps=root.find('RPS')
    signature=etree.Element('Assinatura')
    signature.text=base64.b64encode(key.sign(signature_chain_v2(config,data).encode('ascii'),padding.PKCS1v15(),hashes.SHA1())).decode()
    rps.insert(0,signature)
    key_pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    cert_pem=certificate.public_bytes(serialization.Encoding.PEM)
    signer=MunicipalSigner(method=SignatureConstructionMethod.enveloped,signature_algorithm=SignatureMethod.RSA_SHA1,digest_algorithm=DigestAlgorithm.SHA1,c14n_algorithm=CanonicalizationMethod.CANONICAL_XML_1_0)
    signed=signer.sign(root,key=key_pem,cert=cert_pem)
    schema=etree.XMLSchema(etree.parse(str(BASE/'schemas/official/PedidoEnvioLoteRPS_v02.xsd'),etree.XMLParser(no_network=True,resolve_entities=False)))
    schema.assertValid(signed)
    XMLVerifier().verify(signed,x509_cert=cert_pem,expect_config=SignatureConfiguration(signature_methods=frozenset({SignatureMethod.RSA_SHA1}),digest_algorithms=frozenset({DigestAlgorithm.SHA1})))
    key.public_key().verify(base64.b64decode(signature.text),signature_chain_v2(config,data).encode('ascii'),padding.PKCS1v15(),hashes.SHA1())
    envelope=etree.Element(etree.QName(SOAP,'Envelope'),nsmap={'soap':SOAP})
    body=etree.SubElement(envelope,etree.QName(SOAP,'Body'))
    request=etree.SubElement(body,etree.QName(NFE,'TesteEnvioLoteRPSRequest'))
    etree.SubElement(request,etree.QName(NFE,'VersaoSchema')).text='2'
    etree.SubElement(request,etree.QName(NFE,'MensagemXML')).text=etree.tostring(signed,encoding='unicode')
    return etree.tostring(envelope,encoding='utf-8',xml_declaration=True),key_pem+ b'\n'+cert_pem


def parse_test_response(status, content):
    result={'metodo':'TesteEnvioLoteRPS','emissao_realizada':False,'cancelamento_realizado':False,'contador_alterado':False,'http_status':status,'teste_aprovado':False,'erros':[],'alertas':[]}
    if status!=200:
        result['erros']=[{'codigo':'HTTP_'+str(status),'descricao':'Resposta HTTP não aprovada; não há reenvio automático.'}]
        return result
    try:
        root=etree.fromstring(content,etree.XMLParser(no_network=True,resolve_entities=False))
        bodies=root.findall('{'+SOAP+'}Body')
        if root.tag!='{'+SOAP+'}Envelope' or len(bodies)!=1 or len(bodies[0])!=1:
            raise ValueError('Envelope SOAP ausente ou ambíguo.')
        response=bodies[0][0]
        if response.tag=='{'+SOAP+'}Fault':raise ValueError('SOAP Fault retornado pelo serviço.')
        if response.tag!='{'+NFE+'}TesteEnvioLoteRPSResponse':
            raise ValueError('Resposta não corresponde ao teste municipal solicitado.')
        returns=response.findall('{'+NFE+'}RetornoXML')
        if len(returns)!=1 or not returns[0].text or len(returns[0]):
            raise ValueError('RetornoXML ausente ou ambíguo.')
        inner=etree.fromstring(returns[0].text.encode(),etree.XMLParser(no_network=True,resolve_entities=False))
        if inner.tag!='{'+NFE+'}RetornoEnvioLoteRPS':
            raise ValueError('Documento de retorno municipal inesperado.')
        if any(not isinstance(node.tag, str) or etree.QName(node).namespace for node in inner):
            raise ValueError('Namespace inesperado nos campos do retorno municipal.')
        successes=inner.xpath('./Cabecalho/Sucesso/text()')
        if len(successes)!=1:raise ValueError('Confirmação do teste ausente.')
        for tag,field in [('Erro','erros'),('Alerta','alertas')]:
            for node in inner.findall(tag):
                result[field].append({'codigo':''.join(node.xpath('.//*[local-name()="Codigo"]/text()')),'descricao':''.join(node.xpath('.//*[local-name()="Descricao"]/text()'))})
        result['teste_aprovado']=successes[0].strip().lower()=='true' and not result['erros']
    except (etree.XMLSyntaxError,ValueError) as error:
        result['erros'].append({'codigo':'RESPONSE','descricao':str(error)})
    return result


def send_test_only(envelope, pem_path, *, authorized=False):
    if not authorized:raise PermissionError('Envio de teste não solicitado explicitamente.')
    root=etree.fromstring(envelope,etree.XMLParser(no_network=True,resolve_entities=False))
    bodies=root.findall('{'+SOAP+'}Body')
    if root.tag!='{'+SOAP+'}Envelope' or len(bodies)!=1 or len(bodies[0])!=1 or bodies[0][0].tag!='{'+NFE+'}TesteEnvioLoteRPSRequest':
        raise PermissionError('Somente TesteEnvioLoteRPSRequest é permitido.')
    request=bodies[0][0]
    versions=request.findall('{'+NFE+'}VersaoSchema')
    messages=request.findall('{'+NFE+'}MensagemXML')
    if len(versions)!=1 or versions[0].text!='2':raise PermissionError('Apenas teste municipal schema 2.')
    if len(request)!=2 or len(messages)!=1 or not messages[0].text or len(messages[0]):
        raise PermissionError('MensagemXML do teste ausente ou ambígua.')
    inner=etree.fromstring(messages[0].text.encode(),etree.XMLParser(no_network=True,resolve_entities=False))
    if inner.tag!='{'+NFE+'}PedidoEnvioLoteRPS':
        raise PermissionError('Apenas lote RPS municipal no teste.')
    with requests.Session() as session:
        session.trust_env=False
        return session.post(TEST_ENDPOINT,data=envelope,headers={'Content-Type':'text/xml; charset=utf-8','SOAPAction':TEST_ACTION},cert=str(pem_path),timeout=(15,45),allow_redirects=False)


def run_test(config_file, data_file, *, send=False):
    preview=preview_emission(config_file,data_file,'teste',2)
    if not send or not preview['sucesso']:return preview,None
    config=_read_object(config_file);data=_read_object(data_file)
    password=dotenv_values(BASE/'.env').get('NFSE_CERT_PASSWORD')
    if not password:raise ValueError('Senha local do certificado ausente.')
    certificate_path=Path(config['certificado'])
    if not certificate_path.is_absolute():certificate_path=BASE/certificate_path
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',UserWarning)
        key,certificate,_=pkcs12.load_key_and_certificates(certificate_path.read_bytes(),password.encode())
    envelope,pem=build_signed_test(config,data,key,certificate)
    with tempfile.TemporaryDirectory(prefix='nfse-test-',dir=str(BASE)) as temporary:
        path=Path(temporary)/'tls.pem'
        descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'wb') as file:file.write(pem)
        response=send_test_only(envelope,path,authorized=True)
        result=parse_test_response(response.status_code,response.content)
    return result,response.content


if __name__=='__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='Somente teste municipal: nunca emite ou cancela NFS-e.')
    parser.add_argument('--config',default='config.json');parser.add_argument('--dados',required=True)
    parser.add_argument('--enviar-teste',action='store_true',help='Autoriza exclusivamente TesteEnvioLoteRPS; não altera emissão/cancelamento.')
    args=parser.parse_args()
    result,_=run_test(args.config,args.dados,send=args.enviar_teste)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result.get('teste_aprovado') or result.get('sucesso') else 2)
