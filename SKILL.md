---
name: nota-fiscal-paulistana
description: Faturamento NFS-e SP (Emissão e Cancelamento de Notas Fiscais em São Paulo)
---

<!-- Discriminação padrão: SERVIÇOS PRESTADOS PELOS PRÓPRIOS SÓCIOS NO EXERCÍCIO DE PROFISSÃO REGULAMENTADA POR LEGISLAÇÃO FEDERAL, ISENTO DA RETENÇÃO DO INSS CONFORME PREVISTO NO ART. 120, INCISO III, § 2º DA IN/RFB Nº 971/2009. -->

# Habilidade de Faturamento NFS-e SP (OpenClaw)

## Fluxo municipal v2 com produção controlada

Para rascunho sem rede, use `previsualizar_nfse_municipal.py`. Para teste remoto ou emissão municipal sob autorização, use a entrada separada `emitir_nfse_municipal.py`, conforme [VALIDACAO_MUNICIPAL_V2.md](VALIDACAO_MUNICIPAL_V2.md). Produção é opt-in na configuração local, exige `--modo producao`, arquivo explícito e `id_operacao` estável; inclui consulta de RPS, journal, locks e confirmação independente. `--dry-run` nunca envia, e `--modo teste` nunca gera nota.

Para conferir a política e o próximo RPS sem emitir, execute `python {baseDir}/emitir_nfse_municipal.py --status`. Não inferir MAINTENANCE_LOCK a partir do histórico, de --dry-run ou de frases adicionadas por print/echo.

Esse fluxo não substitui nem bloqueia os emissores/cancelador legados. Sua configuração de produção e seu journal são locais e não devem ser publicados. Ativar a capacidade não autoriza emitir rascunhos antigos ou retomar automações. Consulte a seção de produção controlada antes de qualquer operação real ou recuperação após timeout.

Esta skill permite ao agente **emitir, cancelar e consultar** Notas Fiscais de Serviço eletrônicas (NFS-e) da Prefeitura de São Paulo, conversando com o usuário em português.

> **Todos os arquivos da skill devem estar na mesma pasta** (ex.: `workspace/skills/nfse-sp/`).

> ## 🗂️ DOIS LAYOUTS COEXISTEM — pergunte ao usuário qual usar
>
> | Layout | Script | Quando | Status |
> |---|---|---|---|
> | **Layout 1** (só ISS + PCC) | `emitir_nfse.py` | Válido em 2026; não destaca IBS/CBS | ✅ Validado |
> | **Layout 2** (com IBSCBS) | `emitir_nfse_v2.py` | Válido em 2026 (IBS/CBS informativos); **obrigatório em 2027** | ✅ Validado |
>
> Em **2026 os dois são válidos** (posição oficial da Prefeitura) e o destaque de IBS/CBS é **facultativo** — a LC 214/2025 dispensa o *recolhimento* este ano. **A decisão de qual layout usar é tomada no "PASSO ZERO" da Seção 1** (que manda perguntar ao usuário quando não houver preferência). A partir de **2027 o Layout 2 é obrigatório**.

---

## 📁 Arquivos do Ecossistema
| # | Arquivo | Função |
|---|---|---|
| 1 | `emitir_nfse.py` | **Emissor do Layout 1** (PCC 2026) — monta o XML SOAP, assina e envia |
| 2 | `emitir_nfse_v2.py` | **Emissor do Layout 2** (IBSCBS) — pronto para 2027. Tem `--selftest` e validação local contra XSD |
| 3 | `cancelar_nfse.py` | Cancelamento de notas |
| 4 | `baixar_notas.py` | Extração de relatórios/extratos contábeis (consulta paginada) |
| 5 | `config.json` | Dados da empresa, retenções, alíquotas, bloco `pcc_2026` (Layout 1) e bloco `ibscbs` (Layout 2) |
| 6 | `tomadores.json` | Cadastro de clientes recorrentes (agenda) |
| 7 | `contador_rps.txt` | Controle da sequência do talão (número do RPS) |
| 8 | `Certificados.p12` | Chave criptográfica municipal (**JAMAIS EXPOR**) |
| 9 | `.env` | Arquivo oculto com a variável `NFSE_CERT_PASSWORD=senha` |
| 10 | `SP_PCC_2026.md` | Documentação do Layout 1 / sistemática PCC |
| 11 | `MIGRATION_RTC_2026.md`, `RTC_2026_README.md` | Documentação do Layout 2 / IBSCBS |
| 12 | `schemas_oficiais_sp/` | XSDs oficiais v02 (validação local) + **Anexo VIII** (tabela de códigos NBS/cIndOp/cClassTrib) |
| 13 | `fontes_oficiais_prefeitura/` | Manual do WebService v3.3 e XSDs oficiais arquivados |

---

## 🗓️ Contexto Tributário 2026 (LEIA ANTES DE EMITIR)

A partir de 2026 há **duas frentes de mudança** em paralelo. Conheça ambas para responder ao usuário e calcular os valores certos.

### Frente 1 — Layout 1 com a sistemática PCC ✅ em vigor

Mudança da Prefeitura SP no significado dos campos de tributos federais, vigente desde **01/01/2026** (validada na homologação: `"sucesso": true`). Já implementada no `emitir_nfse.py`:

| Campo XML | Antes (até 2025) | Agora (2026+) |
|---|---|---|
| `<ValorPIS>` | Valor retido (só se houvesse retenção) | **Débito próprio** — sempre preenchido (Lucro Presumido: 0,65%) |
| `<ValorCOFINS>` | Valor retido | **Débito próprio** — sempre preenchido (3%) |
| `<ValorCSLL>` | Retenção de CSLL apenas (1%) | **Soma das retenções PCC** (PIS+COFINS+CSLL = 4,65%), quando há retenção |
| `<ValorIR>` | IRRF retido (1,5%) | Inalterado |
| `<TipoRetencao>` | Não existia | Anunciado, mas o webservice **ainda não aceita** — desligado pela flag `pcc_2026.emitir_tipo_retencao=false` |

**Conceitos que você deve saber explicar:**
- **Débito próprio** = imposto que o prestador apura e paga por conta própria (DARF mensal). Independe de quem é o tomador.
- **Retenção** = imposto que a fonte pagadora desconta e recolhe em nome do prestador (vira crédito antecipado dele).
- A retenção PCC (4,65%) feita por operadoras/clientes PJ **quita** o PIS/COFINS próprios do mês; a CSLL pode deixar diferença trimestral.

### Frente 2 — Layout 2 (IBSCBS / Reforma Federal) ✅ implementado

Reforma Tributária do Consumo (EC 132/2023 + LC 214/2025): cria **CBS** (substitui PIS/COFINS) e **IBS** (substitui ISS/ICMS), no grupo XML `<IBSCBS>`. Emissor: `emitir_nfse_v2.py` (Seção 7).

- Validado contra o XSD oficial e a homologação SP (`"sucesso": true`), no mesmo endpoint, com `VersaoSchema=2`.
- Em 2026 os valores de IBS/CBS são informativos e **não há recolhimento** (LC 214) — por isso o Layout 1 segue plenamente válido.
- **Cronograma:** 2026 ano-teste → 2027 CBS valendo e PIS/COFINS extintos → 2029-2032 transição ISS→IBS → 2033 IBS pleno.

### Resumo prático (situação → ação)

| Situação | Ação |
|---|---|
| Pedido de emissão | Resolva o **PASSO ZERO** (Seção 1): qual layout. Em 2026, pergunte se não houver preferência |
| Usuário pergunta sobre IBSCBS / Reforma | Implementado e pronto; em 2026 sem recolhimento (LC 214); obrigatório em 2027 |
| "Por que `ValorPIS` aparece sem retenção?" | É o **débito próprio**, em vigor desde 2026 |
| "Por que `ValorCSLL` está maior?" | Agora é a **soma PCC** (4,65%), não só CSLL (1%) |
| Montar o payload de uma nota | **Sempre** `calcular_retencoes: true`; **nunca** escreva retenção na `discriminacao` (Seção 2) |
| Nota para **PF sem intermediário** | Sem retenção — só débito próprio. O script zera IRRF/PCC; o esboço não mostra retenção |
| Nota **sem tomador, com intermediário** (operadora) | **Tem retenção** — o intermediário é a fonte pagadora PJ. Preencha o bloco `intermediario` (Seção 2, Modelo B) |
| PJ que não retém (ex.: Simples) | Adicione `"tomador_retem": false` no JSON |
| Erro 1001 citando `<TipoRetencao>` | Confirme que `emitir_tipo_retencao` está `false` no config |
| Usuário não sabe o NBS/cIndOp/cClassTrib | Consulte o Anexo VIII (Seção 7) |

---

## 0. Wizard de Instalação (primeiro uso)

Sempre que o usuário solicitar uma ação financeira pela primeira vez (ou se notar algo faltando), faça um check-up silencioso lendo o `config.json`. Se houver palavras-chave genéricas (`"MEUCNPJ"`, `"Minhainscricao"`, `"MEUCertificado.p12"`) ou `aliquota_servicos` = `0.0`, o usuário acabou de instalar a skill. Pause a tarefa e inicie um **Wizard de Instalação amigável**:

0. **Dependências (obrigatório):** rode `pip install -r requirements.txt` para garantir `requests`, `lxml`, `signxml`, `cryptography` e `python-dotenv`. **Não emita sem esta instalação bem-sucedida.**
1. Diga que percebeu ser a primeira vez e peça, um por vez: CNPJ, Inscrição Municipal, Código de Serviço e a **Alíquota de ISS**.

> [!IMPORTANT]
> **Bloqueio de segurança na alíquota:** aguarde obrigatoriamente a resposta do usuário.
> - **Não avance** com o padrão 2% (0.02) por conta própria se o arquivo tiver 0.0.
> - **Pergunte:** *"Qual a alíquota de ISS para o seu código de serviço? Posso ajudar a descobrir."*
> - **Ajuda ativa:** se não souber, pesquise cruzando o código de serviço com as alíquotas de SP e sugira: *"Encontrei que para o serviço X a alíquota em SP costuma ser Y%. Confirma?"*
> - **Só prossiga** quando o usuário informar um número ou confirmar a sugestão.

2. A cada resposta, **você mesmo** grava os dados no `config.json`.
3. **Prepare o `.env`:** copie (ou renomeie) o modelo `env.example` para `.env` (oculto) na pasta, pronto para a senha.
4. **Privacidade do `.env`:** você **JAMAIS** lê o conteúdo do `.env` para conferir a senha. Parta do princípio de que foi preenchida e diga: *"Por segurança, não tenho permissão para ler seu arquivo .env. Vou confiar que você inseriu a senha e seguir com o teste."*
5. **Certificado e senha (etapa manual do usuário):** oriente exatamente assim:
   > *"Pronto, preenchi os dados da empresa! Agora, por segurança, faça a última etapa manualmente: abra a pasta da skill no seu computador e arraste para lá o seu certificado (ex.: `Certificado.p12`). Depois abra o arquivo oculto `.env` (no Mac, `Command + Shift + .` mostra arquivos ocultos), troque o lado direito de `NFSE_CERT_PASSWORD=` pela senha verdadeira do certificado, salve e me avise."*
6. Quando o usuário confirmar, atualize no `config.json` o nome exato do `.p12` copiado e siga para o teste.
7. **"Batismo de Fogo" (teste obrigatório):** antes de concluir a instalação, faça uma emissão de teste para validar a assinatura e a conexão.
   - Gere silenciosamente `/tmp/test_instalacao.json` com: `valor_servicos` 150.00, `indicador_tomador` 2, `documento_tomador` "00000000000191", `razao_social_tomador` "CLIENTE TESTE - OPENCLAW", `iss_retido` "N", `discriminacao` "Teste tecnico de integracao e assinatura digital".
   - Rode: `python3 emitir_nfse.py --modo teste --dados /tmp/test_instalacao.json --json-out`
   - Se `sucesso: true`, parabenize: *"Sua instalação foi validada em modo teste. Já pode emitir notas reais!"*
   - Se der erro, analise o código retornado e ajude a corrigir (ex.: 1056/1057 → cheque Inscrição Municipal ou senha).
   - **NUNCA** incremente o `contador_rps.txt` após um teste, com ou sem sucesso.

---

## 1. Fluxo de Emissão

> ### 🟢 PASSO ZERO — qual layout? (decida ANTES de montar a nota)
> Defina o layout nesta ordem de prioridade:
> 1. **Data ≥ 01/01/2027?** → **Layout 2** (`emitir_nfse_v2.py`), obrigatório. Não pergunte.
> 2. **Existe `"layout_preferido"` no `config.json`?** → respeite ("1" ou "2"). Não pergunte.
> 3. **O usuário já disse o layout?** → use o que ele disse.
> 4. **Senão (2026, sem preferência):** **PERGUNTE**, de forma curta:
>    > *"Em qual layout quer emitir?*
>    > *• **Layout 1** — tradicional, só ISS. Mais simples. Válido em 2026.*
>    > *• **Layout 2** — já com IBS/CBS (Reforma). Em 2026 os valores são informativos, você não paga nada a mais; vira obrigatório em 2027 (bom 'ensaio').*
>    > *Qual prefere?"*
>
> Se o usuário fixar uma preferência ("sempre Layout 2"), **grave** `"layout_preferido"` no `config.json` e pare de perguntar. Se pedir recomendação, sugira a **abordagem híbrida** (emitir real no Layout 1 + testar o Layout 2 em `--modo teste`). Para o Layout 2, confirme o enquadramento fiscal antes da 1ª emissão real (Seção 7).
>
> **Layout 1 → `emitir_nfse.py`** (este fluxo). **Layout 2 → `emitir_nfse_v2.py`** (Seção 7 para payload e códigos). As 6 etapas abaixo valem para os dois.

**1. Recepção do pedido:** o usuário pede a nota (valor e tomador). Ex.: "Nota de 1500 para a AMIL". Resolva o PASSO ZERO antes de prosseguir.

**2. Triagem (`tomadores.json`):** leia o cadastro. Se o tomador já existe, puxe CNPJ, endereço e e-mail de lá. Se for novo, peça os dados faltantes.

**3. Simulação financeira (esboço):** calcule os impostos cruzando com o `config.json` e apresente um esboço claro:

> | Item | Alíquota | Valor (R$) |
> |---|---:|---:|
> | **Valor bruto dos serviços** | — | X,XX |
> | **Débitos próprios (informativos):** | | |
> | ↳ PIS | 0,65% | informativo |
> | ↳ COFINS | 3,00% | informativo |
> | **Retenções (descontam do recebido):** | | |
> | ↳ Retenção PCC consolidada | 4,65% | −X,XX |
> | ↳ IRRF | 1,50% | −X,XX |
> | **Valor líquido a receber** | | X,XX |
> | ISS (destacado, não retido no padrão) | 2% | X,XX |
>
> **Explique ao usuário:** PIS e COFINS são *débitos próprios* (aparecem na nota desde 2026 mesmo sem retenção). Quando há fonte pagadora PJ, ela retém o PCC (4,65%) e recolhe em nome do prestador, o que quita o PIS/COFINS do mês (a CSLL pode sobrar diferença trimestral).
>
> ⚠️ **Quando NÃO há retenção:** se a nota for para **Pessoa Física pura** (sem intermediário), **remova as linhas de retenção** do esboço — mostre só bruto, débitos próprios, ISS e líquido (= bruto). **Se houver intermediário** (operadora que paga), **mantenha as retenções** mesmo sem tomador identificado. O script aplica a regra sozinho; o esboço deve refletir. (Detalhes na Seção 2.)

**4. Oitiva humana:** pergunte se o usuário **aprova o faturamento**.

**5. Emissão e RPS:**
- Leia o `contador_rps.txt` para pegar o próximo número X.
- Gere `/tmp/dados_rps_X.json` (Seção 2; no Layout 2 use `valor_final_cobrado` no lugar de `valor_servicos`).
- Execute conforme o layout do PASSO ZERO:
  - **Layout 1:** `python emitir_nfse.py --modo producao --dados /tmp/dados_rps_X.json --json-out`
  - **Layout 2:** `python emitir_nfse_v2.py --modo producao --dados /tmp/dados_rps_X.json --json-out`
- Incremente o `contador_rps.txt` (+1) imediatamente.
- Exclua o `/tmp/dados_rps_X.json` ao final, para manter o sistema limpo.

> **Flags de teste (ambos os scripts aceitam):**
> - `--modo teste` → envia para o ambiente de **homologação** da Prefeitura (valida tudo, mas **não emite** nota real).
> - `--dry-run` → monta e assina o XML **localmente** e **não envia nada** (nem para homologação). Útil para conferir o XML/payload sem rede. **Em teste/dry-run, NUNCA incremente o `contador_rps.txt`.**

**6. Entrega:** leia a saída JSON (Seção 5) e devolva ao usuário: o sucesso, o **número da NF-e** e a **URL oficial do PDF**. Como a Prefeitura bloqueia o envio público, invoque a **Skill GOG (e-mails)** para enviar o link do PDF ao próprio e-mail do usuário. Se o tomador for novo e aprovado, salve-o no `tomadores.json`.

---

## 2. Geração do Payload JSON (Layout 1)

Para o passo 5, gere um `/tmp/dados_rps_XXX.json` exclusivo para o atendimento.

> [!IMPORTANT]
> **SANITIZAÇÃO OBRIGATÓRIA (evita o erro de assinatura / 1057).**
> A Prefeitura valida a assinatura comparando o XML com um digest interno; acentos e quebras de linha quebram essa comparação. A regra vale para **os campos que VOCÊ (agente) preenche no JSON**:
> 1. **Remova acentos e `ç`** de `razao_social_tomador`, `discriminacao` e campos de endereço (`logradouro`, `complemento`, `bairro`). Ex.: `SAÚDE → SAUDE`, `SERVIÇOS → SERVICOS`.
> 2. **Remova quebras de linha** (`\n`, `\r`) da `discriminacao`, juntando tudo em um parágrafo único.
>
> A `mensagem_padrao` do `config.json` (texto fixo que o script anexa à discriminação) é responsabilidade do config — não precisa sanitizá-la a cada nota; se ela causar erro de assinatura, ajuste-a uma vez no `config.json`.

> [!IMPORTANT]
> **RETENÇÕES VÃO NOS CAMPOS PRÓPRIOS — nunca apenas no texto.**
> As retenções (PIS/COFINS/CSLL/IRRF) são gravadas nos campos `<ValorPIS/COFINS/CSLL/IR>`, e **o script os preenche automaticamente**. Você não calcula nem escreve retenção na mão. Regras:
> 1. **Sempre** inclua `"calcular_retencoes": true`. Sem isso, os campos saem em branco (o erro a evitar). O script lê as alíquotas do `config.json` e preenche sozinho.
> 2. **Nunca** coloque valores de imposto na `discriminacao` — ela é só o texto do serviço (+ a `mensagem_padrao`, que o script anexa).
> 3. **Quem retém é a fonte pagadora PJ.** Há retenção quando: o **tomador é PJ** (`indicador_tomador: 2`) **OU** há **intermediário** (bloco `intermediario` com CNPJ — operadora/plataforma que paga e retém, mesmo sem tomador identificado). **Não há** retenção para **PF** (`1`) ou exterior (`4`) **sem** intermediário — nesse caso fica só o débito próprio PIS/COFINS. Override: `"tomador_retem": true|false` (ex.: PJ do Simples → `false`).
> 4. **Piso de R$ 10:** mesmo com fonte pagadora PJ, o script dispensa a retenção abaixo do mínimo legal (CSRF e IRRF de R$ 10 — Lei 10.833 art. 31). Notas de valor baixo podem sair sem retenção: é correto. O débito próprio é mantido.
> 5. **Confira** ao final: `calcular_retencoes: true`, nenhum imposto na `discriminacao`, e `intermediario` preenchido quando o pagamento vem via operadora.

### Modelo A — Tomador identificado (PJ ou PF)
```json
{
  "numero_rps": <Lido_do_contador_rps.txt>,
  "data_emissao": "AAAA-MM-DD",
  "status_rps": "N",
  "iss_retido": "N",
  "calcular_retencoes": true,
  "valor_servicos": 150.00,
  "indicador_tomador": 2, // 2=CNPJ (PJ, retém) | 1=CPF (PF, não retém sem intermediário) | 3=Sem ID | 4=NIF/exterior
  "documento_tomador": "<Apenas_Numeros>",
  // "tomador_retem": false,  // (opcional) só p/ PJ que não retém, ex. Simples Nacional
  "razao_social_tomador": "<Nome_ou_Razao_Social_SEM_ACENTOS>",
  "email_tomador": "<Email_Cliente>",
  // endereco_tomador: OBRIGATÓRIO só para tomador PJ (CNPJ). Para PF (CPF),
  // OMITA o bloco inteiro se não tiver o endereço — NUNCA invente dados falsos.
  "endereco_tomador": {
      "logradouro": "RUA X", "numero": "123", "bairro": "VILA Y", "cidade": "3550308", "uf": "SP", "cep": "00000000"
  },
  "discriminacao": "<Texto_do_servico_SEM_ACENTOS>"
}
```
> **Endereço:** inclua o bloco `endereco_tomador` para **PJ (CNPJ)**. Para **PF (CPF)**, ele é opcional — se o usuário não informou o endereço, **omita o bloco** (não preencha com "NAO INFORMADO" nem dados inventados).

### Modelo B — SEM tomador identificado, COM intermediário
Para serviços pagos por **operadora/plataforma** (ex.: plano de saúde), sem tomador final identificado. O **intermediário é a fonte pagadora PJ e retém** — por isso `calcular_retencoes` segue `true`.
```json
{
  "numero_rps": <Lido_do_contador_rps.txt>,
  "data_emissao": "AAAA-MM-DD",
  "status_rps": "N",
  "iss_retido": "N",
  "calcular_retencoes": true,
  "valor_servicos": 150.00,
  "indicador_tomador": 3,            // 3 = Sem identificação do tomador
  // NÃO inclua documento_tomador nem endereco_tomador (tomador vazio).
  // razao_social_tomador é opcional (ex.: "NAO INFORMADO").
  "intermediario": {
      "cnpj": "<CNPJ_da_operadora>",          // só números — é a fonte pagadora que retém
      "inscricao_municipal": "<IM_ou_vazio>",  // opcional
      "iss_retido": false                       // true se o intermediário retém o ISS
  },
  "discriminacao": "<Texto_do_servico>"
}
```
> Com `indicador_tomador: 3` o script não gera o bloco `<CPFCNPJTomador>` (tomador vazio), mas inclui o intermediário e **calcula as retenções** (há fonte pagadora PJ). Sempre preencha o `cnpj` do intermediário — é ele que dispara a retenção.

---

## 3. Cancelamento de NF-e
Quando o usuário pedir "cancele a nota N":
1. Confirme: *"Deseja mesmo revogar definitivamente a Nota SP nº N?"*
2. Se sim: `python cancelar_nfse.py [N] --json-out`
3. Leia o JSON e informe se o cancelamento foi aceito pela Prefeitura.

---

## 4. Relatórios Contábeis (extrato de notas)
Quando o usuário pedir um relatório/balanço (ex.: "feche a contabilidade do mês passado"), use `baixar_notas.py`:
1. **Por dias:** `python baixar_notas.py --dias X` (padrão 30).
2. **Por período exato:** `python baixar_notas.py --inicio AAAA-MM-DD --fim AAAA-MM-DD` (o script fatia janelas > 30 dias sozinho).
3. **Resumo:** leia o stdout (`Valor Faturado (Bruto Ativo)` e `Notas Ativas`) e dê um panorama humano do fechamento.
4. **Exportação:** o resultado vai para `nfse_contabilidade.json`. Se o usuário quiser ("manda pro contador/meu e-mail"), use a Skill GOG e anexe esse arquivo.

---

## 5. Tratamento da Saída (stdout JSON)
Os scripts sempre retornam JSON. Emissão:
```json
{ "sucesso": true, "notas_geradas": [{"numero": "8952", "url_pdf": "https://..."}] }
```
Cancelamento:
```json
{ "sucesso": true, "mensagem": "NF-e 642 cancelada com sucesso!" }
```
Faça o parsing e formule respostas humanas e completas no chat. Nunca despeje JSON puro ao usuário, a menos que ele peça.

---

## 6. Códigos de Erro da Prefeitura SP
Quando o retorno for `"sucesso": false`, traduza para linguagem simples e proponha a correção:

| Código | Causa provável | Ação |
|:---:|---|---|
| **260** | CEP do tomador inválido | Validar contra CEPs reais de SP (ex.: `01310100`) |
| **268** | Código NBS inválido (Layout 2) | Pegar o NBS correto no Anexo VIII e remover os pontos (Seção 7) |
| **630** | Código indicador de operação inexistente (Layout 2) | Pegar o `cIndOp` correto no Anexo VIII (Seção 7) |
| **640** | `ValorInicialCobrado` indisponível (Layout 2) | Usar `valor_final_cobrado` no JSON (o script já faz) |
| **1001** | XML incompatível com o schema | Ver qual campo foi rejeitado; a mensagem lista os elementos válidos esperados. Para `<TipoRetencao>`, confirme `emitir_tipo_retencao=false` |
| **1050** | Certificado inválido | Verificar validade do `.p12` (ver abaixo); renovar com a AC se expirado |
| **1056 / 1057** | Inscrição municipal ou assinatura RPS incorreta | Conferir IM no portal; verificar acentos/quebras na discriminação (Seção 2) |
| **1206** | Intermediário em formato inválido | Verificar o bloco `intermediario` no JSON |

### 🛡️ Validação preventiva do certificado
**Antes** de qualquer emissão (se não checou recentemente), valide a expiração do certificado:
```bash
./.venv/bin/python -c "from cryptography.hazmat.primitives.serialization import pkcs12; from dotenv import load_dotenv; import os; load_dotenv(); print(pkcs12.load_key_and_certificates(open('Certificados.p12','rb').read(), os.environ['NFSE_CERT_PASSWORD'].encode())[1].not_valid_after_utc)"
```
Se faltarem **menos de 30 dias**, avise o usuário proativamente para renovar.

---

## 7. Layout 2 (IBSCBS) — manual operacional

> Use esta seção quando o PASSO ZERO (Seção 1) definir Layout 2 — a pedido do usuário em 2026, ou automaticamente a partir de 2027.

### Como emitir
`emitir_nfse_v2.py` tem as mesmas flags do v1, mais `--selftest` e `--no-validate`:
```bash
python emitir_nfse_v2.py --selftest                                   # valida a assinatura (offline)
python emitir_nfse_v2.py --modo teste --dados nota.json --dry-run     # monta + valida XML vs XSD (offline)
python emitir_nfse_v2.py --modo teste --dados nota.json --json-out    # teste real (homologação, não emite)
python emitir_nfse_v2.py --modo producao --dados nota.json --json-out # emissão REAL
```
O script **valida o XML contra o XSD oficial** (`schemas_oficiais_sp/xsd_completo/`) antes de enviar — corrija erros de estrutura antes de gastar requisição.

### Diferenças do payload (v2)
O RPS v2 **não tem** `valor_servicos`. Use:
- `valor_final_cobrado` (valor total da nota, com tributos) — substitui `valor_servicos`
- `valor_pis`, `valor_cofins`, `valor_inss`, `valor_ir`, `valor_csll` (já calculados; o script aplica a regra de fonte pagadora PJ da Seção 2)
- `valor_ipi` (0 p/ serviços), `exigibilidade_suspensa` (0), `pagamento_parcelado_antecipado` (0)
- Os códigos IBSCBS (`nbs`, `c_ind_op`, `cclasstrib`) vêm do `config.json` → bloco `ibscbs`

### 🔎 Como achar os códigos NBS / cIndOp / cClassTrib
São 3 códigos que classificam o serviço para o IBS/CBS. Busque-os assim:
1. Abra a tabela oficial arquivada: `schemas_oficiais_sp/AnexoVIII-Correlacao-Item-NBS-IndOp-cClassTrib_*.xlsx` (ou baixe de [gov.br/nfse → doc. técnica → RTC](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/rtc)).
2. Leia a aba **"tabela geral"** (com openpyxl). Colunas: `Item LC 116 | Descrição | NBS | INDOP | cClassTrib`.
3. Filtre pelo **Item da LC 116** do serviço (medicina = grupo `04.xx`) ou pela descrição.
4. Extraia **NBS**, **INDOP** (=cIndOp) e **cClassTrib**.
5. Formate o NBS sem pontos (`1.2301.22.00` → `123012200`) e grave no `config.json` → `ibscbs`.
6. **Sempre confirme o enquadramento com o usuário/contador** — o NBS muda por especialidade.

```python
import openpyxl
wb = openpyxl.load_workbook('schemas_oficiais_sp/AnexoVIII-Correlacao-Item-NBS-IndOp-cClassTrib_v1.01.00.xlsx', data_only=True)
ws = wb['tabela geral']
for r in ws.iter_rows(values_only=True):
    if r[0] and str(r[0]).startswith('04.'):   # serviços de saúde
        print(r[0], '| NBS', r[2], '| cIndOp', r[6], '| cClassTrib', r[8])
```

### Valores já validados (MEDICINA — item 04.01, no config por padrão)
| Campo | Valor | Significado |
|---|---|---|
| `nbs` | `123012200` | Serviços médicos especializados (NBS 1.2301.22.00) |
| `c_ind_op` | `030101` | Local de incidência: local da prestação |
| `cclasstrib` | `200029` | Saúde humana (Anexo III) — **com redução de alíquota** |

> ⚠️ **Saúde não é tributação integral.** Use `cClassTrib 200029` (redução ~60%), nunca `000001`. Os itens 04.01 (medicina) e 04.03 (clínica/hospital) têm a **mesma tributação** — muda só o NBS. Veterinária (05.01) é diferente (`200052`, redução 30%); planos de saúde (04.22) usam regime próprio (`820001`).

Os erros específicos do Layout 2 (268, 630, 640) estão no catálogo da Seção 6.
