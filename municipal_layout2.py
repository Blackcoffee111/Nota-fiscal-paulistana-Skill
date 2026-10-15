"""Unsigned São Paulo municipal schema-v2 drafts. No transport or signing."""
from pathlib import Path
from decimal import Decimal, InvalidOperation
from lxml import etree

BASE = Path(__file__).resolve().parent
NS = 'http://www.prefeitura.sp.gov.br/nfe'

class MunicipalRuleError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)



def money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number.as_tuple().exponent < -2:
            raise ValueError('Valores monetários devem ser finitos, não negativos e ter até duas casas decimais.')
        return format(number, '.2f')
    except InvalidOperation:
        raise ValueError('Valor monetário inválido.') from None


def municipal_preview(config, data):
    if data.get('intermediario'):
        raise ValueError('Intermediário requer implementação específica; não omitir dados informados.')
    if 'numero_dps' in data:
        raise ValueError('Layout 2 municipal usa numero_rps, não numero_dps.')
    if data.get('calcular_retencoes'):
        raise ValueError('Cálculo automático de retenções pendente; informar valores revisados explicitamente.')
    settings = dict(config.get('campos_layout_2') or {})
    overrides = data.get('ibscbs', {})
    if not isinstance(overrides, dict) or set(overrides) - set(settings):
        raise ValueError('Bloco ibscbs inválido ou com campos fora do contrato municipal.')
    settings.update(overrides)
    for field in ['cClassTrib','cIndOp','NBS','indFinal','finNFSe','ExigibilidadeSuspensa','indDest']:
        if settings.get(field) is None or settings.get(field) == '':
            raise ValueError('Campo municipal obrigatório sem decisão: ' + field)
    classification = str(settings['cClassTrib'])
    regular_value = settings.get('cClassTribReg')
    regular_required = classification.startswith('550')
    if regular_required and regular_value in (None, ''):
        raise MunicipalRuleError('659', 'Classificação principal prefixo 550 exige cClassTribReg explícito.')
    if not regular_required and regular_value not in (None, ''):
        raise MunicipalRuleError('282', 'cClassTribReg não permitido neste perfil sem suspensão; omitir o bloco regular, sem alterar a classificação principal.')
    operation = str(settings['cIndOp']).strip()
    if len(operation) == 5 and operation.isascii() and operation.isdigit():
        operation = operation.zfill(6)
    settings['cIndOp'] = operation
    for field, allowed in [('finNFSe',(0,)),('indFinal',(0,1)),('ExigibilidadeSuspensa',(0,1)),('indDest',(0,1))]:
        value = settings[field]
        text = str(int(value)) if isinstance(value,bool) else str(value)
        if text not in {str(code) for code in allowed}:
            raise ValueError('Indicador municipal inválido: '+field)
        settings[field] = int(text)
    if settings.get('tpOper') is not None and str(settings['tpOper']) not in {'1','2','3','4','5'}:
        raise ValueError('tpOper vazio deve ser omitido; quando informado, usar código de 1 a 5.')

    if settings.get('prestacao') not in ('pais','exterior'):
        raise ValueError('Informar prestacao=pais ou exterior.')
    local_field = 'cLocPrestacao' if settings['prestacao']=='pais' else 'cPaisPrestacao'
    other_field = 'cPaisPrestacao' if local_field=='cLocPrestacao' else 'cLocPrestacao'
    if not settings.get(local_field) or settings.get(other_field) not in (None,''):
        raise ValueError('Informar exclusivamente município no país ou país no exterior.')
    if 'valor_inicial_cobrado' in data:
        raise MunicipalRuleError('640', 'ValorInicialCobrado descontinuado no WebService; informar valor_final_cobrado explicitamente, sem conversão automática.')
    if data.get('valor_final_cobrado') in (None, ''):
        raise MunicipalRuleError('272', 'Informar valor_final_cobrado; não inferir de valor_servicos.')
    if Decimal(money(data['valor_final_cobrado'])) <= 0:
        raise MunicipalRuleError('303', 'valor_final_cobrado deve ser superior a zero.')
    amount_fields = ['valor_final_cobrado']
    for field in ['numero_rps','data_emissao','documento_tomador','discriminacao','valor_deducoes','valor_ipi','iss_retido']:
        if field not in data or data[field] in (None,''):
            raise ValueError('Campo do RPS obrigatório: '+field)
    if not isinstance(data['iss_retido'],bool):
        raise ValueError('iss_retido deve ser booleano explícito.')
    if not str(data['numero_rps']).isascii() or not str(data['numero_rps']).isdigit() or int(str(data['numero_rps'])) <= 0:
        raise ValueError('numero_rps deve ser positivo e explícito; não há reserva de numeração.')
    retentions = data.get('retencoes')
    if not isinstance(retentions,dict) or any(name not in retentions for name in ('pis','cofins','inss','ir','csll')):
        raise ValueError('Informar retencoes explícitas: pis, cofins, inss, ir, csll.')
    root = etree.Element(etree.QName(NS,'PedidoEnvioLoteRPS'),nsmap={'nfe':NS})
    def add(parent,name,value):
        node=etree.SubElement(parent,name)
        node.text=str(value)
        return node
    header=etree.SubElement(root,'Cabecalho',Versao='2')
    sender=etree.SubElement(header,'CPFCNPJRemetente');add(sender,'CNPJ',config['cnpj_prestador'])
    add(header,'transacao','true');add(header,'dtInicio',data['data_emissao']);add(header,'dtFim',data['data_emissao']);add(header,'QtdRPS',1)
    rps=etree.SubElement(root,'RPS')
    key=etree.SubElement(rps,'ChaveRPS');add(key,'InscricaoPrestador',config['inscricao_municipal']);add(key,'SerieRPS',data.get('serie_rps',config.get('serie_rps','')));add(key,'NumeroRPS',data['numero_rps'])
    add(rps,'TipoRPS',data.get('tipo_rps','RPS'));add(rps,'DataEmissao',data['data_emissao']);add(rps,'StatusRPS','N');add(rps,'TributacaoRPS',data.get('tributacao_rps',config.get('tributacao_rps','T')))
    add(rps,'ValorDeducoes',money(data['valor_deducoes']))
    for name,field in [('ValorPIS','pis'),('ValorCOFINS','cofins'),('ValorINSS','inss'),('ValorIR','ir'),('ValorCSLL','csll')]:add(rps,name,money(retentions[field]))
    add(rps,'CodigoServico',data.get('codigo_servico',config['codigo_servico']));add(rps,'AliquotaServicos',str(data.get('aliquota_servicos',config['aliquota_servicos'])));add(rps,'ISSRetido',str(data['iss_retido']).lower())
    doc=str(data['documento_tomador']);doc=''.join(c for c in doc if c.isalnum())
    provider=''.join(c for c in str(config['cnpj_prestador']) if c.isalnum())
    if doc.upper()==provider.upper():
        raise MunicipalRuleError('301', 'Tomador é o próprio prestador; informar tomador distinto para testar uma operação válida.')
    tag='CPF' if len(doc)==11 else 'CNPJ' if len(doc)==14 else None
    if tag is None:raise ValueError('Documento do tomador deve ser CPF ou CNPJ no contrato local atual.')
    taker=etree.SubElement(rps,'CPFCNPJTomador');add(taker,tag,doc)
    if data.get('razao_social_tomador'):add(rps,'RazaoSocialTomador',data['razao_social_tomador'])
    address=data.get('endereco_tomador')
    if address:
        node=etree.SubElement(rps,'EnderecoTomador')
        for name,field in [('TipoLogradouro','tipo_logradouro'),('Logradouro','logradouro'),('NumeroEndereco','numero'),('ComplementoEndereco','complemento'),('Bairro','bairro'),('Cidade','cidade'),('UF','uf'),('CEP','cep')]:
            if address.get(field):add(node,name,address[field])
    if data.get('email_tomador'):add(rps,'EmailTomador',data['email_tomador'])
    add(rps,'Discriminacao',data['discriminacao'])
    if data.get('tp_retencao_pis_cofins') is not None:add(rps,'RetencaoPisCofins',data['tp_retencao_pis_cofins'])
    name=amount_fields[0];add(rps,'ValorInicialCobrado' if name=='valor_inicial_cobrado' else 'ValorFinalCobrado',money(data[name]))
    add(rps,'ValorIPI',money(data['valor_ipi']));add(rps,'ExigibilidadeSuspensa',int(settings['ExigibilidadeSuspensa']))
    if settings.get('NCM'):add(rps,'NCM',settings['NCM'])
    add(rps,'NBS',settings['NBS']);add(rps,local_field,settings[local_field])
    ibs=etree.SubElement(rps,'IBSCBS');add(ibs,'finNFSe',int(settings['finNFSe']));add(ibs,'indFinal',int(settings['indFinal']));add(ibs,'cIndOp',settings['cIndOp'])
    if settings.get('tpOper') is not None:add(ibs,'tpOper',settings['tpOper'])
    add(ibs,'indDest',int(settings['indDest']))
    if settings['indDest']==1:
        recipient=data.get('destinatario')
        if not isinstance(recipient,dict) or not recipient.get('documento') or not recipient.get('nome'):
            raise ValueError('indDest=1 exige destinatario com documento e nome explícitos.')
        recipient_doc=''.join(c for c in str(recipient['documento']) if c.isalnum())
        recipient_tag='CPF' if len(recipient_doc)==11 else 'CNPJ' if len(recipient_doc)==14 else None
        if recipient_tag is None:raise ValueError('Documento do destinatário deve ser CPF/CNPJ neste contrato local.')
        destination=etree.SubElement(ibs,'dest');add(destination,recipient_tag,recipient_doc);add(destination,'xNome',recipient['nome'])
        address=recipient.get('endereco')
        if address:
            if any(not address.get(key) for key in ('cidade','cep','logradouro','numero','bairro')):
                raise ValueError('Endereço nacional do destinatário incompleto.')
            end=etree.SubElement(destination,'end');national=etree.SubElement(end,'endNac');add(national,'cMun',address['cidade']);add(national,'CEP',address['cep']);add(end,'xLgr',address['logradouro']);add(end,'nro',address['numero'])
            if address.get('complemento'):add(end,'xCpl',address['complemento'])
            add(end,'xBairro',address['bairro'])
        if recipient.get('email'):add(destination,'email',recipient['email'])
    elif data.get('destinatario'):
        raise ValueError('Bloco destinatario diferente é incompatível com indDest=0.')
    values=etree.SubElement(ibs,'valores');trib=etree.SubElement(values,'trib');group=etree.SubElement(trib,'gIBSCBS');add(group,'cClassTrib',settings['cClassTrib']);
    if regular_required:
        regular=etree.SubElement(group,'gTribRegular');add(regular,'cClassTribReg',regular_value)
    parser=etree.XMLParser(no_network=True,resolve_entities=False)
    schema=etree.XMLSchema(etree.parse(str(BASE/'schemas/preview/PedidoEnvioLoteRPS_v02.xsd'),parser))
    if not schema.validate(root):
        raise ValueError('Estrutura municipal inválida: '+str(schema.error_log.last_error))
    return {'provedor':'prefeitura_sp','versao_schema':2,'schema_sem_assinaturas_validado':True,'assinaturas_validadas':False,'xml_nao_assinado':etree.tostring(root,encoding='unicode'),'campos_segunda_pagina':{'cClassTribReg':regular_value,'cLocPrestacao':settings.get('cLocPrestacao'),'cPaisPrestacao':settings.get('cPaisPrestacao'),'indFinal':settings['indFinal'],'finNFSe':settings['finNFSe'],'tpOper':settings.get('tpOper'),'ExigibilidadeSuspensa':settings['ExigibilidadeSuspensa']},'cIndOp_normalizado':operation,'NBS':settings['NBS']}
