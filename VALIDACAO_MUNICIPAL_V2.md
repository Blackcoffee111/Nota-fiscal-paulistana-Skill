# Prévia, teste e produção controlada municipal v2

Este fluxo adicional prepara RPS municipal de São Paulo com os schemas v02-5. Não substitui automaticamente os emissores existentes. A produção controlada é uma entrada separada e opt-in, descrita abaixo; a integração com os fluxos legados continua fora desse caminho.

`previsualizar_nfse_municipal.py` valida localmente, sem rede, certificado, senha, assinatura ou avanço de contador:

```bash
python previsualizar_nfse_municipal.py --config config-preview.exemplo.json --dados payload-layout2-municipal.exemplo.json
python -m unittest discover -s tests -v
```

Config e payload de exemplo são fictícios, para validação estrutural. Códigos e retenções devem ser escolhidos para cada operação com o responsável fiscal; os valores do exemplo não são padrões universais.

O total deve ser informado explicitamente em `valor_final_cobrado`, positivo. Valor inicial e conversão implícita do antigo `valor_servicos` são rejeitados. `cIndOp` preserva seis dígitos. `indDest=1` exige destinatário explícito. Local de prestação nacional e país estrangeiro são escolhas exclusivas. Os indicadores `indFinal`, `finNFSe` e `ExigibilidadeSuspensa` permanecem no XML. No perfil implementado, `gTribRegular` é omitido para 200029 e exigido para classificação com prefixo 550; outros perfis requerem revisão.

`schemas/preview` omite apenas as duas assinaturas obrigatórias. Validação de prévia não comprova autorização ou assinatura. `schemas/official` conserva os XSD oficiais completos.

`testar_nfse_municipal.py` também faz apenas prévia por padrão. `--enviar-teste` acessa certificado e senha locais e solicita exclusivamente `TesteEnvioLoteRPS`: usar somente após autorização explícita para teste remoto e com dados revisados. Endpoint/action são fixos, sem redirects, proxies de ambiente ou reenvio automático. Não há emissão, cancelamento ou alteração do contador nesse cliente. HTTP 200 sozinho não aprova o teste: exige confirmação municipal sem erros. O módulo `fiscal_safety.py` protege somente os novos fluxos que o invocam; não bloqueia os scripts legados.

Limites: o cliente de teste não implementa emissão; produção usa o runtime separado abaixo. Assinatura com intermediário não está implementada. A validação local rejeita tomador igual ao prestador. Não certifica adequação tributária nem todos os perfis municipais.

## Testes de regressão

São 33 testes locais, sem serviço fiscal ou certificado real. Além do payload e dos schemas, cobrem assinatura RSA-SHA1 e verificação XML com chaves efêmeras, transporte simulado sem reenvio, funcionamento das CLIs fora da pasta da skill e JSON UTF-8 no Windows. A prévia rejeita intermediários ainda não implementados antes de acessar credenciais.

O parser exige Envelope/Body SOAP, TesteEnvioLoteRPSResponse, RetornoXML único e raiz municipal RetornoEnvioLoteRPS. Rejeita respostas de outro método, SOAP Fault, confirmação ambígua e erros municipais. Preserva alertas longos sem convertê-los em erro: há retornos municipais cujo texto excede o limite do XSD arquivado. Essa verificação de protocolo não é uma validação integral do schema de resposta.

Foi concluído separadamente um ciclo autorizado de emissão e cancelamento reais usando o construtor municipal, com consultas independentes. Esse executor controlado não faz parte do PR e não demonstra homologação da integração com os emissores legados. Divergências entre os retornos de consulta e os XSD arquivados permanecem uma pendência desse fluxo separado. Não adicionar respostas reais, documentos fiscais ou dados pessoais aos testes públicos.

Fontes: [manual municipal 3.3.9](https://notadomilhao.sf.prefeitura.sp.gov.br/wp-content/uploads/2026/10/NFe_Web_Service-v3.3.9.pdf), [schemas v02-5](https://notadomilhao.sf.prefeitura.sp.gov.br/schemas-reformatributaria-v02-5).

## Produção controlada municipal v2 (Linux/WSL)

`emitir_nfse_municipal.py` é uma entrada nova; não modifica a execução dos emissores legados (`emitir_nfse.py`, `emitir_nfse_v2.py`) ou do cancelador. Habilitar o fluxo municipal não deve ser confundido com desativar ou revisar todos esses scripts.

A configuração de exemplo mantém `layout2_municipal.envio_habilitado=false`. Após configurar A1 e senha local (`NFSE_CERT_PASSWORD` no `.env`), revisar o prestador e autorizar a ativação, usar uma cópia LOCAL da configuração com essa opção `true`. Não publicar config real, certificado, senha, contador, tomadores ou journal. A1 é verificado por validade, chave e CNPJ; isso não substitui auditoria de revogação/ICP. A conexão TLS mantém verificação normal.

O modo padrão continua `teste`; `--dados` é obrigatório, sem rascunho implícito. Use um arquivo revisado por operação. Prévia (`--dry-run`) continua sem rede, certificado ou contador, inclusive com modo produção:

```bash
python emitir_nfse_municipal.py --layout 2 --modo producao --config /config/local.json --dados /rascunho/revisado.json --dry-run
python emitir_nfse_municipal.py --layout 2 --modo teste --config /config/local.json --dados /rascunho/revisado.json
# Somente depois da autorização para a nota/cobrança identificada:
python emitir_nfse_municipal.py --layout 2 --modo producao --config /config/local.json --dados /rascunho/revisado.json
```

Teste usa somente TesteEnvioLoteRPS e não gera nota. Produção usa somente EnvioLoteRPS e gera documento fiscal válido. O endpoint é o mesmo servidor municipal; a diferença está no método, não em trocar para SEFIN nacional. Esta contribuição não habilita cancelamento na entrada nova. Produção requer Linux/WSL para os locks POSIX; prévia e cliente de teste permanecem portáveis.

O JSON de produção exige `id_operacao` estável da cobrança, além do payload municipal. Use referência de faturamento e competência sem dados de paciente. `numero_rps` deve corresponder ao próximo contador local e a série à configurada. Não alterar a numeração para forçar reenvio. Retenções e valores devem ser explícitos, revisados pelo responsável; não copiar os zeros fictícios do exemplo.

O runtime consulta o RPS antes do envio e recusa número já convertido. Usa locks de processo/contador, reserva por RPS e journal durável em `operacoes_fiscais/` (protegido, ignorado pelo Git). Assina e valida RPS/XML contra XSD oficial e verifica as assinaturas localmente. Grava o estado antes de um único POST, sem redirects, proxies de ambiente ou retry automático. Não inclui e-mails de tomador/destinatário enquanto mensagens estão pausadas.

Só avança o contador após chave de retorno e consulta independente que corresponda à nota solicitada (tomador, total, retenções, serviço e IBS/CBS). Preserva a resposta e os alertas completos; alertas longos não transformam uma emissão confirmada em motivo de reenvio. HTTP 200 sozinho não é sucesso. Divergências fiscais/contábeis não são resolvidas pela validação técnica.

Após timeout ou resultado incerto, **reutilizar o mesmo id_operacao e exatamente o mesmo payload**: a chamada apenas consulta/reconcilia, sem novo POST de produção. Não inventar novo ID/RPS para repetir a cobrança. `NO_RESEND`, reservas e rejeições exigem revisão de journal e situação municipal; não apagar arquivos de controle para contornar a trava. Uma operação confirmada retorna comprovante histórico em cache, identificado por `reenvio_evitado=true` e `status_nao_reconsultado=true`: não é uma consulta do status atual após cancelamento no portal.

A autorização para instalar/ativar não autoriza emitir notas antigas nem retomar agendamentos. Cada execução real deve corresponder ao pedido do usuário. Esta contribuição não controla canais/cron do host; preservá-los conforme as instruções do responsável.

Os novos testes de produção usam journal temporário, transporte simulado e identidades fictícias. Cobrem locks, duplicidade, RPS ocupado, timeout e reconciliação sem reenvio, contador, divergências de valores, método SOAP e campos fiscais. Nenhum desses testes emite/cancela nota, usa certificado real ou chama o município.

Validação desta ampliação: 70 testes aprovados no Linux/WSL. No Windows, 47 aprovados e 23 testes de journal POSIX ignorados explicitamente (produção requer WSL). Nenhuma chamada fiscal integra a suíte pública.

## Diagnóstico verificável de ativação

`python emitir_nfse_municipal.py --status --config /config/local.json` consulta somente a configuração local e o contador da skill; dispensa --dados e não usa certificado, rede, assinatura ou emissão. Retorna `producao_habilitada`, `trava_hardcoded_emissao_layout2`, `proximo_rps` e `serie_rps`. O status informa política/numeração; não substitui o teste municipal nem a revisão fiscal.

Não usar um --dry-run ou texto de print/echo para afirmar que o caminho real retornou MAINTENANCE_LOCK. Após ativação, erros como MESSAGES_PAUSED, OPERATION_ID ou RPS_COUNTER dizem respeito ao payload ou à numeração. Preparar esses campos para a cobrança autorizada; não confundir número da NFS-e com número do RPS nem transmitir uma nota apenas para verificar a ativação.
