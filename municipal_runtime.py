"""Municipal v2 transport. Explicit production, durable journal, no automatic retry."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
try:
    import fcntl
except ImportError:  # Preview and test remain portable; production uses POSIX locks.
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import warnings
import requests
from lxml import etree
from dotenv import dotenv_values
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from testar_nfse_municipal import (BASE, NFE, SOAP, MunicipalSigner, build_signed_test,
    parse_test_response, SignatureConstructionMethod, SignatureMethod, DigestAlgorithm,
    CanonicalizationMethod, XMLVerifier, SignatureConfiguration)

ENDPOINT = 'https://nfews.prefeitura.sp.gov.br/lotenfe.asmx'
METHODS = {'teste': ('TesteEnvioLoteRPS', 'testeenvio'),
           'producao': ('EnvioLoteRPS', 'envioLoteRPS'),
           'consulta': ('ConsultaNFe', 'consultaNFe')}
STATE = BASE / 'operacoes_fiscais'

class FiscalError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

def parser():
    return etree.XMLParser(no_network=True, resolve_entities=False)

def json_write(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def binary_write(path, content):
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

def validate_certificate(config):
    path = Path(config['certificado'])
    if not path.is_absolute(): path = BASE / path
    password = dotenv_values(BASE / '.env').get('NFSE_CERT_PASSWORD')
    if not password: raise FiscalError('CERT_PASSWORD', 'Senha do certificado não disponível localmente.')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        key, certificate, _ = pkcs12.load_key_and_certificates(path.read_bytes(), password.encode())
    if key is None or certificate is None:
        raise FiscalError('CERT_KEY', 'Certificado A1 ou chave privada ausente.')
    now = datetime.now(timezone.utc)
    if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
        raise FiscalError('CERT_VALIDITY', 'Certificado fora da validade.')
    cnpj = str(config['cnpj_prestador'])
    identities = [str(a.value).encode() for a in certificate.subject]
    try:
        identities += [n.value for n in certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                       if isinstance(n, x509.OtherName) and n.type_id.dotted_string == '2.16.76.1.3.3']
    except x509.ExtensionNotFound:
        pass
    if not any(cnpj.encode() in identity for identity in identities):
        raise FiscalError('CERT_CNPJ', 'Certificado não corresponde ao CNPJ do prestador.')
    encoding = serialization.Encoding.DER
    public_format = serialization.PublicFormat.SubjectPublicKeyInfo
    if key.public_key().public_bytes(encoding, public_format) != certificate.public_key().public_bytes(encoding, public_format):
        raise FiscalError('CERT_KEY', 'Chave privada não corresponde ao certificado.')
    return key, certificate

class MunicipalClient:
    def __init__(self, config):
        self.config = config
        self.key, self.certificate = validate_certificate(config)

    @contextmanager
    def connection(self):
        private = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption())
        public = self.certificate.public_bytes(serialization.Encoding.PEM)
        with tempfile.TemporaryDirectory(prefix='nfse-tls-') as directory:
            path = Path(directory) / 'tls.pem'
            binary_write(path, private + b'\n' + public)
            with requests.Session() as session:
                session.trust_env = False
                self.session, self.tls = session, path
                try: yield self
                finally: self.session, self.tls = None, None

    def post(self, envelope, mode):
        if mode not in METHODS: raise FiscalError('METHOD', 'Método fiscal não permitido.')
        method, action = METHODS[mode]
        root = etree.fromstring(envelope, parser())
        bodies = root.findall('{' + SOAP + '}Body')
        if root.tag != '{' + SOAP + '}Envelope' or len(bodies) != 1 or len(bodies[0]) != 1:
            raise FiscalError('ENVELOPE', 'Envelope SOAP inválido.')
        request = bodies[0][0]
        if request.tag != '{' + NFE + '}' + method + 'Request':
            raise FiscalError('METHOD', 'Envelope não corresponde ao método selecionado.')
        versions = request.findall('{' + NFE + '}VersaoSchema')
        messages = request.findall('{' + NFE + '}MensagemXML')
        if len(request) != 2 or len(versions) != 1 or versions[0].text != '2' or len(messages) != 1 or not messages[0].text or len(messages[0]):
            raise FiscalError('ENVELOPE', 'Schema ou MensagemXML ausente/ambíguo.')
        inner = etree.fromstring(messages[0].text.encode(), parser())
        expected_root = 'PedidoConsultaNFe' if mode == 'consulta' else 'PedidoEnvioLoteRPS'
        if inner.tag != '{' + NFE + '}' + expected_root:
            raise FiscalError('METHOD', 'Documento XML incompatível com o método.')
        response = self.session.post(ENDPOINT, data=envelope,
            headers={'Content-Type': 'text/xml; charset=utf-8',
                     'SOAPAction': '"http://www.prefeitura.sp.gov.br/nfe/ws/' + action + '"'},
            cert=str(self.tls), timeout=(15, 45), allow_redirects=False)
        return response

    def query(self, data, audit_path):
        root = etree.Element('{' + NFE + '}PedidoConsultaNFe', nsmap={'nfe': NFE})
        h = etree.SubElement(root, 'Cabecalho', Versao='2')
        doc = etree.SubElement(h, 'CPFCNPJRemetente')
        etree.SubElement(doc, 'CNPJ').text = str(self.config['cnpj_prestador'])
        detail = etree.SubElement(root, 'Detalhe')
        k = etree.SubElement(detail, 'ChaveRPS')
        for name, value in [('InscricaoPrestador', self.config['inscricao_municipal']),
                ('SerieRPS', data.get('serie_rps', self.config['serie_rps'])), ('NumeroRPS', data['numero_rps'])]:
            etree.SubElement(k, name).text = str(value)
        kp = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        cp = self.certificate.public_bytes(serialization.Encoding.PEM)
        signer = MunicipalSigner(method=SignatureConstructionMethod.enveloped,
            signature_algorithm=SignatureMethod.RSA_SHA1, digest_algorithm=DigestAlgorithm.SHA1,
            c14n_algorithm=CanonicalizationMethod.CANONICAL_XML_1_0)
        signed = signer.sign(root, key=kp, cert=cp)
        etree.XMLSchema(etree.parse(str(BASE / 'schemas/official/PedidoConsultaNFe_v02.xsd'), parser())).assertValid(signed)
        XMLVerifier().verify(signed, x509_cert=cp, expect_config=SignatureConfiguration(
            signature_methods=frozenset({SignatureMethod.RSA_SHA1}), digest_algorithms=frozenset({DigestAlgorithm.SHA1})))
        response = self.post(wrap(signed, 'consulta'), 'consulta')
        binary_write(audit_path, response.content)
        inner = response_inner(response.status_code, response.content, 'RetornoConsulta')
        if inner.findtext('Cabecalho/Sucesso') != 'true' or inner.findall('Erro'):
            raise FiscalError('QUERY_FAILED', 'Consulta municipal não aprovada; não enviar produção.')
        notes = inner.findall('NFe')
        if not notes:
            if not any(a.findtext('Codigo') == '1106' for a in inner.findall('Alerta')):
                raise FiscalError('QUERY_AMBIGUOUS', 'Ausência de nota sem confirmação municipal 1106.')
            return None
        if len(notes) != 1: raise FiscalError('QUERY_AMBIGUOUS', 'Consulta retornou múltiplas notas.')
        return notes[0]

    def emission(self, data, mode):
        envelope, _ = build_signed_test(self.config, data, self.key, self.certificate)
        if mode == 'producao':
            root = etree.fromstring(envelope, parser())
            root.find('{' + SOAP + '}Body')[0].tag = '{' + NFE + '}EnvioLoteRPSRequest'
            envelope = etree.tostring(root, encoding='utf-8', xml_declaration=True)
        return envelope

def wrap(signed, mode):
    root = etree.Element('{' + SOAP + '}Envelope', nsmap={'soap': SOAP})
    b = etree.SubElement(root, '{' + SOAP + '}Body')
    request = etree.SubElement(b, '{' + NFE + '}' + METHODS[mode][0] + 'Request')
    etree.SubElement(request, '{' + NFE + '}VersaoSchema').text = '2'
    etree.SubElement(request, '{' + NFE + '}MensagemXML').text = etree.tostring(signed, encoding='unicode')
    return etree.tostring(root, encoding='utf-8', xml_declaration=True)

def response_inner(status, content, name):
    if status != 200: raise FiscalError('HTTP', 'HTTP não aprovado; não reenviar automaticamente.')
    root = etree.fromstring(content, parser())
    bodies = root.findall('{' + SOAP + '}Body')
    if root.tag != '{' + SOAP + '}Envelope' or len(bodies) != 1 or len(bodies[0]) != 1:
        raise FiscalError('RESPONSE', 'Envelope SOAP ausente ou ambíguo.')
    response = bodies[0][0]
    expected_method = {'RetornoConsulta': 'ConsultaNFeResponse',
                       'RetornoEnvioLoteRPS': 'EnvioLoteRPSResponse'}[name]
    if response.tag != '{' + NFE + '}' + expected_method:
        raise FiscalError('RESPONSE', 'Método de resposta não corresponde à solicitação.')
    returns = response.findall('{' + NFE + '}RetornoXML')
    if len(returns) != 1 or not returns[0].text or len(returns[0]):
        raise FiscalError('RESPONSE', 'RetornoXML ausente ou ambíguo.')
    inner = etree.fromstring(returns[0].text.encode(), parser())
    if inner.tag != '{' + NFE + '}' + name:
        raise FiscalError('RESPONSE', 'Tipo de documento de retorno inválido.')
    if any(not isinstance(node.tag, str) or etree.QName(node).namespace for node in inner):
        raise FiscalError('RESPONSE', 'Namespace inesperado nos campos de retorno.')
    headers = inner.findall('Cabecalho')
    successes = inner.findall('Cabecalho/Sucesso')
    if len(headers) != 1 or len(successes) != 1 or successes[0].text not in ('true', 'false'):
        raise FiscalError('RESPONSE', 'Confirmação municipal ausente ou ambígua.')
    # The municipality returns alerts longer than its XSD maxLength=300.
    # Preserve the raw response and all alerts; validate fiscal keys and query separately.
    return inner

def events(inner, tag):
    return [{'codigo': e.findtext('Codigo'), 'descricao': e.findtext('Descricao')} for e in inner.findall(tag)]

def confirm_note(note, config, data):
    expected = {'ChaveNFe/InscricaoPrestador': str(config['inscricao_municipal']),
                'ChaveRPS/InscricaoPrestador': str(config['inscricao_municipal']),
                'ChaveRPS/SerieRPS': str(data.get('serie_rps', config['serie_rps'])),
                'ChaveRPS/NumeroRPS': str(data['numero_rps']),
                'CodigoServico': str(int(data.get('codigo_servico', config['codigo_servico']))),
                'Discriminacao': data['discriminacao'], 'ISSRetido': str(data['iss_retido']).lower()}
    settings = dict(config.get('campos_layout_2', {}))
    settings.update(data.get('ibscbs', {}))
    expected['NBS'] = str(settings['NBS'])
    for name in ('finNFSe', 'indFinal', 'cIndOp', 'indDest'):
        expected['IBSCBS/' + name] = str(settings[name]).zfill(6) if name == 'cIndOp' else str(int(settings[name]))
    expected['IBSCBS/valores/trib/gIBSCBS/cClassTrib'] = str(settings['cClassTrib'])
    for field, value in expected.items():
        if note.findtext(field) != value: raise FiscalError('NOTE_MISMATCH', 'Consulta não corresponde ao pedido: ' + field)
    doc = ''.join(c for c in str(data['documento_tomador']) if c.isalnum())
    if note.findtext('CPFCNPJTomador/' + ('CPF' if len(doc) == 11 else 'CNPJ')) != doc:
        raise FiscalError('NOTE_MISMATCH', 'Tomador retornado não corresponde ao pedido.')
    for field, expected_value in [('ValorFinalCobrado', data['valor_final_cobrado']),
         ('ValorDeducoes', data['valor_deducoes']), ('ValorIPI', data['valor_ipi']),
         ('AliquotaServicos', data.get('aliquota_servicos', config['aliquota_servicos']))] + [
         (field, data['retencoes'][key]) for field, key in [('ValorPIS','pis'), ('ValorCOFINS','cofins'),
             ('ValorINSS','inss'), ('ValorIR','ir'), ('ValorCSLL','csll')]]:
        value = note.findtext(field)
        if Decimal(value or '0') != Decimal(str(expected_value)):
            raise FiscalError('NOTE_MISMATCH', 'Valor retornado não corresponde ao pedido: ' + field)
    if note.findtext('StatusNFe') != 'N': raise FiscalError('NOTE_STATUS', 'Nota encontrada não está em situação normal.')
    number, code = note.findtext('ChaveNFe/NumeroNFe'), note.findtext('ChaveNFe/CodigoVerificacao')
    if not number or not number.isdigit() or not code: raise FiscalError('NOTE_KEY', 'Chave fiscal incompleta.')
    return {'numero_nfse': number, 'codigo_verificacao': code, 'rps': str(data['numero_rps'])}

def production(config, data, client, xml_hash):
    if fcntl is None:
        raise FiscalError('WSL_REQUIRED', 'Produção requer Linux/WSL para os locks de numeração e journal.')
    operation = data.get('id_operacao')
    if not isinstance(operation, str) or not operation.strip() or len(operation) > 200:
        raise FiscalError('OPERATION_ID', 'Produção exige id_operacao estável da cobrança; reutilizar a mesma identificação nas consultas.')
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    op_hash = hashlib.sha256((str(config['cnpj_prestador']) + ':' + operation).encode()).hexdigest()
    ledger = STATE / (op_hash + '.json')
    lock_path = STATE / '.lock'
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, 'r+') as lock, (BASE / 'contador_rps.txt').open('r+') as counter:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(counter, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FiscalError('BUSY', 'Outra operação fiscal está em andamento.')
        current = int(counter.read().strip())
        old = json.loads(ledger.read_text()) if ledger.exists() else None
        if old and old['xml_hash'] != xml_hash:
            raise FiscalError('OPERATION_CHANGED', 'id_operacao já registrado com outro payload; revisar o registro antes de alterar.')
        if old and old['status'] == 'confirmed':
            result = dict(old['result'])
            result.update(reenvio_evitado=True, rede_utilizada=False, emissao_realizada_nesta_chamada=False, status_nao_reconsultado=True)
            return result
        number = int(str(data['numero_rps']))
        if not old and number != current:
            raise FiscalError('RPS_COUNTER', 'numero_rps deve corresponder ao próximo contador local: ' + str(current))
        audit = STATE / op_hash
        audit.mkdir(mode=0o700, exist_ok=True)
        def finish(note):
            key = confirm_note(note, config, data)
            # Require a consulted note; never advance on HTTP 200 alone.
            counter.seek(0); counter.write(str(max(current, number + 1)) + '\n')
            counter.truncate(); counter.flush(); os.fsync(counter.fileno())
            result = {'sucesso': True, 'emissao_realizada': True, 'emissao_realizada_nesta_chamada': not bool(old),
                'confirmada_por_consulta': True, 'rede_utilizada': True, 'layout': 2, 'modo': 'producao',
                'cancelamento_realizado': False, 'contador_alterado': current <= number,
                'proximo_rps': max(current, number + 1), 'id_operacao': operation,
                'alertas': record.get('alertas', []), **key}
            record.update(status='confirmed', result=result)
            json_write(ledger, record)
            return result
        record = old or {'id_operacao': operation, 'xml_hash': xml_hash, 'rps': str(number), 'status': 'prepared'}
        with client.connection():
            note = client.query(data, audit / ('reconcile.xml' if old else 'preflight.xml'))
            if old:
                if note is not None: return finish(note)
                raise FiscalError('NO_RESEND', 'Operação já registrada; nota ainda não localizada. Não reenviar. Revisar journal e consulta municipal.')
            if note is not None: raise FiscalError('RPS_USED', 'RPS já convertido; não será emitida outra nota.')
            rps_key = hashlib.sha256((str(config['inscricao_municipal']) + ':' +
                str(data.get('serie_rps', config['serie_rps'])) + ':' + str(number)).encode()).hexdigest()
            reservation = STATE / ('rps-' + rps_key + '.json')
            if reservation.exists(): raise FiscalError('RPS_RESERVED', 'RPS reservado por outra operação; reconciliar antes de novo envio.')
            envelope = client.emission(data, 'producao')
            binary_write(audit / 'request.xml', envelope)
            json_write(reservation, {'id_operacao': operation, 'xml_hash': xml_hash})
            record['status'] = 'sending'
            json_write(ledger, record)  # Durable before the only production POST.
            try:
                response = client.post(envelope, 'producao')
                binary_write(audit / 'response.xml', response.content)
                inner = response_inner(response.status_code, response.content, 'RetornoEnvioLoteRPS')
                record['alertas'] = events(inner, 'Alerta')
                record['erros'] = events(inner, 'Erro')
                record['status'] = 'received'
                json_write(ledger, record)
                if inner.findtext('Cabecalho/Sucesso') != 'true' or record['erros']:
                    if inner.findall('ChaveNFeRPS'):
                        raise FiscalError('RESPONSE_AMBIGUOUS', 'Resposta com erros e chave fiscal; consultar RPS.')
                    record['status'] = 'rejected'
                    json_write(ledger, record)
                    return {'sucesso': False, 'emissao_realizada': False, 'erros': record['erros'],
                            'alertas': record['alertas'], 'rede_utilizada': True, 'contador_alterado': False,
                            'id_operacao': operation, 'reenvio_automatico': False}
                mappings = inner.findall('ChaveNFeRPS')
                if len(mappings) != 1: raise FiscalError('RESPONSE_KEY', 'Chave fiscal ausente ou ambígua.')
                mapping = mappings[0]
                for field, value in [('InscricaoPrestador', str(config['inscricao_municipal'])),
                     ('SerieRPS', str(data.get('serie_rps', config['serie_rps']))), ('NumeroRPS', str(number))]:
                    if mapping.findtext('ChaveRPS/' + field) != value:
                        raise FiscalError('RESPONSE_KEY', 'Chave de RPS divergente.')
                note = client.query(data, audit / 'confirmation.xml')
                if note is None: raise FiscalError('NOT_CONFIRMED', 'Nota ainda não disponível para confirmação; não reenviar.')
                if mapping.findtext('ChaveNFe/NumeroNFe') != note.findtext('ChaveNFe/NumeroNFe'):
                    raise FiscalError('RESPONSE_KEY', 'Número de nota divergente da consulta.')
                return finish(note)
            except Exception:
                record['status'] = 'uncertain'
                json_write(ledger, record)
                raise FiscalError('UNCERTAIN', 'Resultado incerto. Reexecutar com MESMO id_operacao e payload apenas consulta; não haverá reenvio.') from None

def valid_document(value):
    digits = ''.join(c for c in str(value) if c.isalnum())
    if not digits.isascii() or not digits.isdigit() or len(set(digits)) == 1:
        return False
    numbers = [int(c) for c in digits]
    if len(numbers) == 14:
        weights = ([5,4,3,2,9,8,7,6,5,4,3,2], [6,5,4,3,2,9,8,7,6,5,4,3,2])
        base = numbers[:12]
        for weight in weights:
            remainder = sum(a*b for a,b in zip(base, weight)) % 11
            base.append(0 if remainder < 2 else 11-remainder)
        return numbers == base
    if len(numbers) == 11:
        base = numbers[:9]
        for start in (10, 11):
            value = (sum(a*b for a,b in zip(base, range(start,1,-1))) * 10) % 11
            base.append(0 if value == 10 else value)
        return numbers == base
    return False


def execute(config, data, mode, preview):
    if not valid_document(config.get('cnpj_prestador')) or not valid_document(data.get('documento_tomador')):
        raise FiscalError('DOCUMENT', 'Documento do prestador ou tomador inválido; não enviar dados fictícios.')
    if data.get('email_tomador') or (data.get('destinatario') or {}).get('email'):
        raise FiscalError('MESSAGES_PAUSED', 'Envio de e-mails está pausado; remover e-mail do payload fiscal.')
    if str(data.get('serie_rps', config.get('serie_rps'))) != str(config.get('serie_rps')):
        raise FiscalError('RPS_SERIES', 'Este contador atende somente à série configurada do prestador.')
    settings = config.get('layout2_municipal', {})
    if settings.get('endpoint') != ENDPOINT or settings.get('provedor') != 'prefeitura_sp' or settings.get('versao_schema') != 2:
        raise FiscalError('PROVIDER', 'Configuração deve usar WebService municipal SP schema 2 e endpoint oficial fixo.')
    if mode == 'producao' and settings.get('envio_habilitado') is not True:
        raise FiscalError('PRODUCTION_DISABLED', 'Produção desativada na configuração municipal.')
    if mode == 'producao' and fcntl is None:
        raise FiscalError('WSL_REQUIRED', 'Produção requer Linux/WSL; prévia e teste continuam disponíveis no Windows.')
    if mode == 'producao' and not data.get('id_operacao'):
        raise FiscalError('OPERATION_ID', 'Produção exige id_operacao estável da cobrança.')
    client = MunicipalClient(config)
    if mode == 'teste':
        with client.connection():
            response = client.post(client.emission(data, 'teste'), 'teste')
            result = parse_test_response(response.status_code, response.content)
            result.update(sucesso=result['teste_aprovado'], rede_utilizada=True, layout=2, modo='teste')
            return result
    digest = hashlib.sha256(preview['xml_nao_assinado'].encode()).hexdigest()
    return production(config, data, client, digest)
