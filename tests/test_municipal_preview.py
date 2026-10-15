import json
from pathlib import Path
import unittest
from unittest.mock import patch
from lxml import etree
from municipal_layout2 import municipal_preview, MunicipalRuleError
from testar_nfse_municipal import run_test, send_test_only, signature_chain_v2, parse_test_response

BASE = Path(__file__).resolve().parents[1]

class MunicipalPreviewTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((BASE / 'config-preview.exemplo.json').read_text(encoding='utf-8'))
        self.data = json.loads((BASE / 'payload-layout2-municipal.exemplo.json').read_text(encoding='utf-8'))

    def test_preview_is_offline_and_does_not_read_credentials_or_counter(self):
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.suffix.lower() in ('.p12', '.pfx', '.pem') or path.name in ('.env', 'contador_rps.txt'):
                raise AssertionError('Sensitive file access')
            return original(path, *args, **kwargs)
        with patch('socket.socket', side_effect=AssertionError('Network access')), patch.object(Path, 'open', guarded):
            result, response = run_test(str(BASE / 'config-preview.exemplo.json'), str(BASE / 'payload-layout2-municipal.exemplo.json'))
        self.assertTrue(result['sucesso'])
        self.assertFalse(result['rede_utilizada'])
        self.assertFalse(result['assinatura_realizada'])
        self.assertFalse(result['contador_alterado'])
        self.assertIsNone(response)

    def test_unsigned_xml_fails_complete_official_schema(self):
        result = municipal_preview(self.config, self.data)
        root = etree.fromstring(result['xml_nao_assinado'].encode())
        schema = etree.XMLSchema(etree.parse(str(BASE / 'schemas/official/PedidoEnvioLoteRPS_v02.xsd')))
        self.assertFalse(schema.validate(root))
        self.assertEqual(root.find('Cabecalho').get('Versao'), '2')

    def test_preview_schemas_remove_only_two_required_signatures(self):
        namespaces = {'xs': 'http://www.w3.org/2001/XMLSchema'}
        changes = {
            'PedidoEnvioLoteRPS_v02.xsd': '//xs:element[@ref="ds:Signature"]',
            'TiposNFe_v02.xsd': '//xs:complexType[@name="tpRPS"]//xs:element[@name="Assinatura"]',
        }
        for name, xpath in changes.items():
            parser = etree.XMLParser(remove_blank_text=True)
            original = etree.parse(str(BASE / 'schemas/official' / name), parser)
            preview = etree.parse(str(BASE / 'schemas/preview' / name), parser)
            matches = original.xpath(xpath, namespaces=namespaces)
            self.assertEqual(len(matches), 1)
            matches[0].getparent().remove(matches[0])
            self.assertEqual(etree.tostring(original, method='c14n'), etree.tostring(preview, method='c14n'))

    def test_initial_value_rejected_even_with_final_value(self):
        self.data['valor_inicial_cobrado'] = '100.00'
        with self.assertRaises(MunicipalRuleError) as caught:
            municipal_preview(self.config, self.data)
        self.assertEqual(caught.exception.code, '640')

    def test_missing_or_nonpositive_final_value(self):
        for value, code in [(None, '272'), ('0.00', '303')]:
            with self.subTest(value=value):
                self.data['valor_final_cobrado'] = value
                with self.assertRaises(MunicipalRuleError) as caught:
                    municipal_preview(self.config, self.data)
                self.assertEqual(caught.exception.code, code)

    def test_legacy_amount_not_inferred(self):
        del self.data['valor_final_cobrado']
        self.data['valor_servicos'] = '100.00'
        with self.assertRaises(MunicipalRuleError):
            municipal_preview(self.config, self.data)

    def test_formatted_self_taker_rejected(self):
        self.data['documento_tomador'] = '00.000.000/0001-91'
        with self.assertRaises(MunicipalRuleError) as caught:
            municipal_preview(self.config, self.data)
        self.assertEqual(caught.exception.code, '301')

    def test_regular_classification_profiles(self):
        self.data['ibscbs']['cClassTribReg'] = '200029'
        with self.assertRaises(MunicipalRuleError) as caught:
            municipal_preview(self.config, self.data)
        self.assertEqual(caught.exception.code, '282')
        self.data['ibscbs']['cClassTrib'] = '550001'
        self.data['ibscbs']['cClassTribReg'] = None
        with self.assertRaises(MunicipalRuleError) as caught:
            municipal_preview(self.config, self.data)
        self.assertEqual(caught.exception.code, '659')
        self.data['ibscbs']['cClassTribReg'] = '200029'
        root = etree.fromstring(municipal_preview(self.config, self.data)['xml_nao_assinado'].encode())
        self.assertEqual(root.findtext('.//gTribRegular/cClassTribReg'), '200029')

    def test_second_page_fields_and_xml_escaping(self):
        self.data['discriminacao'] = 'Serviço A & B <teste>'
        self.data['ibscbs']['cIndOp'] = '30104'
        root = etree.fromstring(municipal_preview(self.config, self.data)['xml_nao_assinado'].encode())
        for name, expected in [('cIndOp','030104'),('indFinal','0'),('finNFSe','0'),('indDest','0'),('ExigibilidadeSuspensa','0'),('cLocPrestacao','3550308')]:
            self.assertEqual(root.findtext('.//' + name), expected)
        self.assertEqual(root.findtext('.//Discriminacao'), self.data['discriminacao'])
        self.assertIsNone(root.find('.//tpOper'))
        self.assertIsNone(root.find('.//NCM'))
        self.assertIsNone(root.find('.//gTribRegular'))

    def test_foreign_and_national_location_are_exclusive(self):
        self.data['ibscbs']['cPaisPrestacao'] = 'US'
        with self.assertRaises(ValueError):
            municipal_preview(self.config, self.data)

    def test_distinct_recipient_requires_identity_and_complete_address(self):
        self.data['ibscbs']['indDest'] = 1
        with self.assertRaises(ValueError):
            municipal_preview(self.config, self.data)
        self.data['destinatario'] = {'documento': '11111111111', 'nome': 'FICTICIO', 'endereco': {'cidade': '3550308'}}
        with self.assertRaises(ValueError):
            municipal_preview(self.config, self.data)
        del self.data['destinatario']['endereco']
        root = etree.fromstring(municipal_preview(self.config, self.data)['xml_nao_assinado'].encode())
        self.assertEqual(root.findtext('.//dest/CPF'), '11111111111')

    def test_invalid_money_and_automatic_retention_rejected(self):
        for value in ['NaN', 'Infinity', '-1.00', '100.001']:
            with self.subTest(value=value):
                self.data['valor_final_cobrado'] = value
                with self.assertRaises(ValueError):
                    municipal_preview(self.config, self.data)
        self.data['valor_final_cobrado'] = '100.00'
        self.data['calcular_retencoes'] = True
        with self.assertRaises(ValueError):
            municipal_preview(self.config, self.data)

    def test_signature_chain_is_90_characters_and_rejects_intermediary(self):
        self.assertEqual(len(signature_chain_v2(self.config, self.data)), 90)
        self.data['intermediario'] = {'documento': '11111111111111'}
        with self.assertRaises(ValueError):
            signature_chain_v2(self.config, self.data)

    def test_remote_request_requires_explicit_authorization(self):
        with self.assertRaises(PermissionError):
            send_test_only(b'<invalid/>', 'unused.pem')

    def test_production_and_cancellation_rejected_before_network(self):
        for operation in ['EnvioLoteRPSRequest', 'CancelamentoNFeRequest']:
            xml = ('<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:n="http://www.prefeitura.sp.gov.br/nfe"><soap:Body><n:' + operation + '><n:VersaoSchema>2</n:VersaoSchema></n:' + operation + '></soap:Body></soap:Envelope>').encode()
            with self.assertRaises(PermissionError):
                send_test_only(xml, 'unused.pem', authorized=True)

    def test_http_200_alone_does_not_approve_test(self):
        for status, body in [(200,b'<html/>'), (500,b'<html/>'), (200,b'not XML')]:
            result = parse_test_response(status, body)
            self.assertFalse(result['teste_aprovado'])
            self.assertTrue(result['erros'])

if __name__ == '__main__':
    unittest.main()
