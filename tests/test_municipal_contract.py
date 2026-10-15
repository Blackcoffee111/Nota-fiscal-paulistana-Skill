"""Regressions with fictional payloads and ephemeral keys; no fiscal network."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from municipal_layout2 import municipal_preview
from testar_nfse_municipal import (
    BASE, NFE, SOAP, TEST_ENDPOINT, TEST_ACTION,
    build_signed_test, parse_test_response, run_test, send_test_only,
)


def response(inner, method='TesteEnvioLoteRPSResponse'):
    root = etree.Element(etree.QName(SOAP, 'Envelope'))
    body = etree.SubElement(root, etree.QName(SOAP, 'Body'))
    result = etree.SubElement(body, etree.QName(NFE, method))
    etree.SubElement(result, etree.QName(NFE, 'RetornoXML')).text = inner
    return etree.tostring(root)


def result_xml(success='true', events=''):
    return ('<n:RetornoEnvioLoteRPS xmlns:n="' + NFE + '">'
            '<Cabecalho Versao="2"><Sucesso>' + success + '</Sucesso></Cabecalho>'
            + events + '</n:RetornoEnvioLoteRPS>')


class MunicipalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'LOCAL TEST ONLY')])
        now = datetime.now(timezone.utc)
        cls.certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                           .public_key(cls.key.public_key()).serial_number(x509.random_serial_number())
                           .not_valid_before(now - timedelta(days=1))
                           .not_valid_after(now + timedelta(days=1)).sign(cls.key, hashes.SHA256()))

    def setUp(self):
        self.config = json.loads((BASE / 'config-preview.exemplo.json').read_text(encoding='utf-8'))
        self.data = json.loads((BASE / 'payload-layout2-municipal.exemplo.json').read_text(encoding='utf-8'))

    def test_intermediary_is_not_silently_omitted_by_preview(self):
        self.data['intermediario'] = {'documento': '11111111111111'}
        with self.assertRaises(ValueError):
            municipal_preview(self.config, self.data)

    def test_invalid_preview_stops_before_remote_credentials(self):
        with patch('testar_nfse_municipal.preview_emission', return_value={'sucesso': False}), \
             patch('testar_nfse_municipal.dotenv_values', side_effect=AssertionError('Secret access')):
            result, content = run_test('unused.json', 'unused.json', send=True)
        self.assertFalse(result['sucesso'])
        self.assertIsNone(content)

    def test_signed_request_passes_full_schema_and_crypto_verification(self):
        self.data['discriminacao'] = 'Serviço fictício A & B <teste>'
        with patch('socket.socket', side_effect=AssertionError('Network access')):
            payload, pem = build_signed_test(self.config, self.data, self.key, self.certificate)
        root = etree.fromstring(payload)
        request = root.find('{' + SOAP + '}Body')[0]
        self.assertEqual(request.tag, '{' + NFE + '}TesteEnvioLoteRPSRequest')
        inner = etree.fromstring(request.findtext('{' + NFE + '}MensagemXML').encode())
        self.assertIsNotNone(inner.find('RPS/Assinatura'))
        self.assertIsNotNone(inner.find('{http://www.w3.org/2000/09/xmldsig#}Signature'))
        self.assertEqual(inner.findtext('RPS/Discriminacao'), self.data['discriminacao'])
        self.assertIn(b'CERTIFICATE', pem)

    def test_signed_request_supports_cnpj_without_real_certificate(self):
        self.data['documento_tomador'] = '11111111111111'
        payload, _ = build_signed_test(self.config, self.data, self.key, self.certificate)
        self.assertIn(b'TesteEnvioLoteRPSRequest', payload)

    def test_transport_uses_only_test_action_without_redirects_or_environment_proxy(self):
        payload, _ = build_signed_test(self.config, self.data, self.key, self.certificate)
        session = MagicMock()
        session.post.return_value.status_code = 200
        with patch('testar_nfse_municipal.requests.Session') as factory:
            factory.return_value.__enter__.return_value = session
            send_test_only(payload, 'fictional.pem', authorized=True)
        self.assertFalse(session.trust_env)
        session.post.assert_called_once_with(
            TEST_ENDPOINT, data=payload,
            headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': TEST_ACTION},
            cert='fictional.pem', timeout=(15, 45), allow_redirects=False)

    def test_transport_rejects_missing_message_before_network(self):
        root = etree.Element(etree.QName(SOAP, 'Envelope'))
        body = etree.SubElement(root, etree.QName(SOAP, 'Body'))
        request = etree.SubElement(body, etree.QName(NFE, 'TesteEnvioLoteRPSRequest'))
        etree.SubElement(request, etree.QName(NFE, 'VersaoSchema')).text = '2'
        with patch('testar_nfse_municipal.requests.Session', side_effect=AssertionError('Network access')):
            with self.assertRaises(PermissionError):
                send_test_only(etree.tostring(root), 'fictional.pem', authorized=True)

    def test_transport_does_not_retry_a_timeout(self):
        import requests
        payload, _ = build_signed_test(self.config, self.data, self.key, self.certificate)
        session = MagicMock()
        session.post.side_effect = requests.Timeout('Fictional timeout')
        with patch('testar_nfse_municipal.requests.Session') as factory:
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(requests.Timeout):
                send_test_only(payload, 'fictional.pem', authorized=True)
        self.assertEqual(session.post.call_count, 1)

    def test_valid_test_response_is_approved(self):
        self.assertTrue(parse_test_response(200, response(result_xml()))['teste_aprovado'])

    def test_long_municipal_alert_does_not_fail_or_disappear(self):
        description = 'A' * 324
        inner = result_xml(events='<Alerta><Codigo>1651</Codigo><Descricao>' + description + '</Descricao></Alerta>')
        result = parse_test_response(200, response(inner))
        self.assertTrue(result['teste_aprovado'])
        self.assertEqual(result['alertas'], [{'codigo': '1651', 'descricao': description}])

    def test_error_overrides_success_flag(self):
        inner = result_xml(events='<Erro><Codigo>999</Codigo><Descricao>ERRO FICTICIO</Descricao></Erro>')
        result = parse_test_response(200, response(inner))
        self.assertFalse(result['teste_aprovado'])
        self.assertEqual(result['erros'][0]['codigo'], '999')

    def test_error_in_wrong_namespace_does_not_disappear(self):
        inner = result_xml(events='<n:Erro><n:Codigo>999</n:Codigo><n:Descricao>ERRO FICTICIO</n:Descricao></n:Erro>')
        result = parse_test_response(200, response(inner))
        self.assertFalse(result['teste_aprovado'])
        self.assertTrue(result['erros'])

    def test_wrong_response_method_is_not_approved(self):
        result = parse_test_response(200, response(result_xml(), 'EnvioLoteRPSResponse'))
        self.assertFalse(result['teste_aprovado'])

    def test_wrong_inner_root_is_not_approved(self):
        inner = '<Fake><Cabecalho><Sucesso>true</Sucesso></Cabecalho></Fake>'
        self.assertFalse(parse_test_response(200, response(inner))['teste_aprovado'])

    def test_non_soap_wrapper_is_not_approved(self):
        root = etree.Element('Fake')
        etree.SubElement(root, 'RetornoXML').text = result_xml()
        self.assertFalse(parse_test_response(200, etree.tostring(root))['teste_aprovado'])

    def test_duplicate_returns_are_not_approved(self):
        root = etree.fromstring(response(result_xml()))
        node = root.find('{' + SOAP + '}Body')[0]
        etree.SubElement(node, etree.QName(NFE, 'RetornoXML')).text = result_xml()
        self.assertFalse(parse_test_response(200, etree.tostring(root))['teste_aprovado'])

    def test_soap_fault_is_not_approved(self):
        root = etree.Element(etree.QName(SOAP, 'Envelope'))
        etree.SubElement(etree.SubElement(root, etree.QName(SOAP, 'Body')), etree.QName(SOAP, 'Fault'))
        self.assertFalse(parse_test_response(200, etree.tostring(root))['teste_aprovado'])

    def test_cli_works_from_another_directory(self):
        for script in ['previsualizar_nfse_municipal.py', 'testar_nfse_municipal.py']:
            with self.subTest(script=script):
                command = [sys.executable, str(BASE / script),
                           '--config', 'config-preview.exemplo.json', '--dados', 'payload-layout2-municipal.exemplo.json']
                run = subprocess.run(command, cwd=BASE.parent, capture_output=True, encoding='utf-8')
                self.assertEqual(run.returncode, 0, run.stderr)
                result = json.loads(run.stdout)
                self.assertTrue(result['sucesso'])
                self.assertFalse(result['rede_utilizada'])


if __name__ == '__main__':
    unittest.main()
