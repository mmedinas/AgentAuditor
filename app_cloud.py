# -*- coding: utf-8 -*-
import streamlit as st
import os
import pandas as pd
import docx # pip install python-docx
from io import BytesIO
import re # Para extrair dados do resumo
import altair as alt # Para os gráficos
import time # Para timestamp no nome do arquivo

# Importando as ferramentas da LangChain para a API do Google
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# --- Funções para Ler os Arquivos ---

def read_sp_file(file):
    """Lê o conteúdo de um arquivo .docx (SP) e retorna como texto."""
    try:
        document = docx.Document(file)
        full_text = [para.text for para in document.paragraphs]
        # Adiciona texto das tabelas
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    full_text.append(cell.text)
        return '\n'.join(full_text)
    except Exception as e:
        # Define o erro no estado da sessão para ser exibido na área principal
        st.session_state.read_error = f"Erro ao ler SP ({file.name}): {e}"
        return "" # Retorna vazio em caso de erro

def read_analysis_files(files):
    """Lê múltiplos arquivos .csv ou .xlsx (Listas) e concatena."""
    all_content, file_names = [], []
    for file in files:
        try:
            content = ""
            # Usa o nome base sem extensão para referência interna e no prompt
            file_base_name = os.path.splitext(file.name)[0] 
            if file.name.endswith('.csv'):
                bytes_data = file.getvalue()
                df = pd.read_csv(BytesIO(bytes_data))
                content = df.to_string()
            elif file.name.endswith('.xlsx'):
                bytes_data = file.getvalue()
                # O 'openpyxl' deve estar instalado (pip install openpyxl)
                df = pd.read_excel(BytesIO(bytes_data))
                content = df.to_string()
            
            file_names.append(file_base_name)
            # Adiciona marcador com nome do arquivo no conteúdo enviado para a IA
            all_content.append(f"--- CONTEÚDO DO ARQUIVO: {file_base_name} ---\n{content}\n")
            
        except Exception as e:
            # Define o erro no estado da sessão
            st.session_state.read_error = f"Erro ao ler Lista ({file.name}): {e}"
            return "", [] # Retorna vazio se falhar em algum arquivo
            
    return '\n'.join(all_content), file_names # Retorna nomes também

# --- Prompt Mestre para Auditoria (v5) ---
# Renomeado de MASTER_PROMPT para MASTER_PROMPT_AUDIT
MASTER_PROMPT_AUDIT = """
Sua **ÚNICA TAREFA** é comparar os itens físicos descritos na "Fonte da Verdade (SP)" (tópicos 17-30) com os itens listados nas "Listas de Engenharia".
**NÃO GERE RELATÓRIOS DE KPIs, CPI, SPI, RAG status, protótipos, adesivos, caixas de papelão ou qualquer outra métrica de gerenciamento de projetos.** Foque **EXCLUSIVAMENTE** na comparação dos itens físicos dos arquivos fornecidos.
**REGRAS ESTRITAS:**
1.  **EXTRAÇÃO (SP):** Leia o documento "FONTE DA VERDADE (SP)" abaixo (entre os marcadores). Extraia itens físicos (comprados/fabricados) dos tópicos 17-30. Um item existe se '[X] Sim' ou se houver especificação/descrição/notas.
2.  **COMPARAÇÃO (Listas):** Para cada item da SP, procure-o nos documentos "LISTAS DE ENGENHARIA". Verifique nome, quantidade e especificações. Use o NOME DO ARQUIVO da lista ao reportar.
3.  **INFERÊNCIA (Implícitos):** Identifique itens implícitos necessários (ex: Gerador->Exaustão) e verifique se estão nas listas.
4.  **RELATÓRIO DE PENDÊNCIAS:** Liste **APENAS** as pendências encontradas, usando o formato Markdown abaixo. Se não houver pendências, escreva apenas "Auditoria Concluída. Nenhuma pendência encontrada.".
**FORMATO OBRIGATÓRIO DO RELATÓRIO MARKDOWN:**
### PENDÊNCIAS - ITENS FALTANTES (SP vs Listas)
* **[Item da SP]:** Não encontrado nas Listas.
### PENDÊNCIAS - DISCREPÂNCIAS TÉCNICAS
* **[Item]:** SP diverge da Lista [NomeLista].
    * **SP:** [Especificação SP]
    * **Lista ([NomeLista]):** [Especificação Lista]
### PENDÊNCIAS - DISCREPÂNCIAS DE QUANTIDADE
* **[Item]:** Qtd na SP diverge da Lista [NomeLista].
    * **SP:** Qtd: [X]
    * **Lista ([NomeLista]):** Qtd: [Y]
### ITENS IMPLÍCITOS FALTANTES
* **[Item Implícito]:** Necessário para [Item SP], mas não encontrado.
---
**IMPORTANTE: APÓS o relatório Markdown, adicione a seção de resumo estruturado:**
[RESUMO ESTRUTURADO PARA GRÁFICOS]
| TipoPendencia           | NomeLista                 | DetalheItem                                        |
| :---------------------- | :------------------------ | :------------------------------------------------- |
| FALTANTE                | N/A                       | [Item da SP]                                       |
| DISCREPANCIA_TECNICA    | [NomeLista do Arquivo]    | [Item]                                             |
| DISCREPANCIA_QUANTIDADE | [NomeLista do Arquivo]    | [Item]                                             |
| IMPLICITO_FALTANTE      | N/A                       | [Item Implícito]                                   |
* (Repita para CADA pendência. Use 'N/A' onde aplicável. Use o nome EXATO do arquivo da lista.)
* Se não houver pendências, escreva "Nenhuma".
---
**DOCUMENTOS PARA ANÁLISE (NÃO INVENTE DADOS SE ELES NÃO FOREM FORNECIDOS):**
--- INÍCIO DA FONTE DA VERDADE (SP) ---
{sp_content}
--- FIM DA FONTE DA VERDADE (SP) ---
--- INÍCIO DAS LISTAS DE ENGENHARIA ---
{analysis_content}
--- FIM DAS LISTAS DE ENGENHARIA ---
**INICIE O RELATÓRIO DE AUDITORIA DE PENDÊNCIAS ABAIXO:**
[RELATÓRIO DE AUDITORIA DE PENDÊNCIAS (Markdown)]
"""

# --- NOVO PROMPT 2: Extração de Lista Mestra ---
MASTER_PROMPT_EXTRACT = """
Sua **ÚNICA TAREFA** é atuar como um engenheiro de orçamentos e extrair uma **Lista Mestra de Equipamentos** (Bill of Materials - BOM) do documento "Fonte da Verdade (SP)".

**NÃO GERE RELATÓRIOS DE KPIs, CPI, SPI, etc.** Foque **EXCLUSIVAMENTE** na extração de itens físicos.

**REGRAS ESTRITAS:**
1.  **LEITURA COMPLETA:** Leia **TODO** o documento "FONTE DA VERDADE (SP)" (do início ao fim) para encontrar itens.
2.  **FONTES DE ITENS:**
    * **Fonte A (Listas Finais):** Extraia todos os itens das tabelas de equipamentos explícitas (ex: Tabela A, Tabela B, Item 30.1, Item 30.2).
    * **Fonte B (Texto Corrido):** Extraia todos os itens físicos relevantes mencionados no corpo do texto (ex: "bomba de água", "reservatórios", "pia", "torneira com sensor", "escada de acesso").
3.  **CONSOLIDAÇÃO:** Crie uma **ÚNICA** lista mestra.
4.  **REMOVER DUPLICATAS:** Se um item da "Fonte B" (texto corrido) já estiver claramente listado na "Fonte A" (listas finais), **NÃO** o repita. A lista final deve ser consolidada e sem duplicatas.
5.  **RELATÓRIO DE EXTRAÇÃO:** Apresente a lista consolidada em formato Markdown. Tente agrupar por categoria (ex: Elétricos, Hidráulicos, Mobiliário).

**FORMATO OBRIGATÓRIO DO RELATÓRIO MARKDOWN:**
### Lista Mestra de Equipamentos (Extraída da SP)

#### Categoria: Elétricos
* [Item 1] (Qtd: [Qtd], Especificação: [Breve Espec.])
* [Item 2] (Qtd: [Qtd], Especificação: [Breve Espec.])

#### Categoria: Hidráulicos
* [Item 3] (Qtd: [Qtd], Especificação: [Breve Espec.])
* [Item 4] (Qtd: [Qtd], Especificação: [Breve Espec.])

#### Categoria: Mobiliário
* [Item 5] (Qtd: [Qtd], Especificação: [Breve Espec.])

* (Continue para todas as categorias e itens encontrados)

---
**IMPORTANTE:** **NÃO GERE** a seção [RESUMO ESTRUTURADO PARA GRÁFICOS].
---

**DOCUMENTO PARA ANÁLISE (NÃO INVENTE DADOS SE ELE NÃO FOR FORNECIDO):**

--- INÍCIO DA FONTE DA VERDADE (SP) ---
{sp_content}
--- FIM DA FONTE DA VERDADE (SP) ---

**INICIE A LISTA MESTRA CONSOLIDADA ABAIXO:**
[LISTA MESTRA DE EQUIPAMENTOS (Markdown)]
"""

# --- Função para Parsear o Resumo Estruturado ---
def parse_summary_table(summary_section):
    pendencias = []
    # Regex ajustado para nome da lista e detalhe, mais flexível
    pattern = r"\|\s*(FALTANTE|DISCREPANCIA_TECNICA|DISCREPANCIA_QUANTIDADE|IMPLICITO_FALTANTE)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    lines = summary_section.strip().split('\n')
    if len(lines) > 2:
        data_lines = lines[2:] # Pula header e linha de separação ----
        for line in data_lines:
            match = re.search(pattern, line, re.IGNORECASE) # Ignora case para N/A e tipo
            if match:
                tipo = match.group(1).strip().upper() # Garante tipo em maiúsculas
                lista_raw = match.group(2).strip()
                detalhe = match.group(3).strip()

                if lista_raw.upper() == 'N/A':
                    lista_clean = 'Geral/Não Encontrado'
                else:
                    # Tenta limpar nome do arquivo (remove path, extensão, etc.)
                    lista_base = os.path.basename(lista_raw)
                    lista_clean = os.path.splitext(lista_base)[0]
                    # Tenta pegar apenas a sigla inicial (LME, LMM, LMH) se aplicável
                    base_name_match = re.match(r"([a-zA-Z]+)(_|\d|-|$)", lista_clean)
                    if base_name_match:
                         lista_clean = base_name_match.group(1) # Usa só a sigla tipo LME
                    else:
                         lista_clean = lista_raw # Mantem nome se não identificar sigla

                pendencias.append({"Tipo": tipo, "Lista": lista_clean, "Item": detalhe})
    return pd.DataFrame(pendencias)

# --- Função para converter DataFrame para CSV (necessária para download) ---
@st.cache_data # Cache para evitar reprocessamento desnecessário
def convert_df_to_csv(df):
    # Garante que o dataframe não está vazio antes de converter
    if df is None or df.empty:
        return "".encode('utf-8')
    return df.to_csv(index=False).encode('utf-8')

# --- Configuração da Página e CSS ---
st.set_page_config(page_title="Agente Auditor v6", layout="wide") # v6 agora

# CSS para moldura (aplicada apenas na área principal agora)
frame_css = """
<style>
/* Estilo base da moldura */
/*.frame {
    border: 1px solid #e1e4e8; border-radius: 6px; padding: 1rem;
    background-color: #ffa804; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    margin-bottom: 1rem; min-height: 400px; *//* Altura mínima para a área de resultados */
}
/* Estilo dos títulos dentro da moldura principal */
.frame h3, .frame h4, .frame h5 {
    margin-top: 0; margin-bottom: 0.8rem; color: #0366d6;
    border-bottom: 1px solid #eaecef; padding-bottom: 0.3rem;
}
/* Oculta a label "select file" padrão */
.stFileUploader label {
 display: none;
}
/* Estilo para subheaders na sidebar */
.st-emotion-cache-16txtl3 h3, .st-emotion-cache-16txtl3 h6 { /* Seletores podem mudar com versões do Streamlit */
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #eaecef;
    margin-bottom: 0.8rem;
    color: #0366d6; /* Aplica cor azul aos títulos da sidebar também */
}
/* Tenta garantir que a sidebar tenha um fundo consistente */
[data-testid="stSidebar"] {
    background-color: #ffa804; /* Cor de fundo levemente cinza para a sidebar */
}
</style>
"""
st.markdown(frame_css, unsafe_allow_html=True)

# --- Inicializa Session State ---
if 'read_error' not in st.session_state: st.session_state.read_error = None
if 'audit_results' not in st.session_state: st.session_state.audit_results = None
if 'start_audit_clicked' not in st.session_state: st.session_state.start_audit_clicked = False
if 'extract_results' not in st.session_state: st.session_state.extract_results = None # NOVO
if 'start_extract_clicked' not in st.session_state: st.session_state.start_extract_clicked = False # NOVO
# Chaves para resetar uploaders
if 'sp_file_uploader_key' not in st.session_state: st.session_state.sp_file_uploader_key = 0
if 'lm_uploader_key' not in st.session_state: st.session_state.lm_uploader_key = 0


# --- Header (Removido da moldura) ---
st.title("🤖✨ Agente Auditor V6")
#st.caption("Auditoria SP vs. Listas & Extração de Lista Mestra | Gemini Cloud")

# --- Sidebar (Inputs e Ações) ---
with st.sidebar:
    st.image("https://raw.githubusercontent.com/mmedinas/AgentAuditor/main/LOGO_MOBILE.png", width=150)
    #st.header("⚙️ Controles")
    
    st.subheader("Chave API")
    google_api_key_from_secrets = os.getenv("GOOGLE_API_KEY")
    if google_api_key_from_secrets:
        st.caption("🔒 Chave API configurada (via Segredos/Ambiente).")
    else:
        st.caption("⚠️ Chave API NÃO configurada. Configure em 'Settings > Secrets'.")

    st.markdown("---")
    
    st.subheader("📄 UPLOADS")
    st.markdown("###### Documento de Entrada (SP)")
    sp_file = st.file_uploader("Upload .docx", type=["docx"], key=f"sp_uploader_{st.session_state.sp_file_uploader_key}", label_visibility="collapsed")

    st.markdown("###### Listas de Engenharia (LMM, LME, LMH)")
    analysis_files = st.file_uploader("Upload .xlsx, .csv", type=["xlsx", "csv"],
                                      accept_multiple_files=True, key=f"lm_uploader_{st.session_state.lm_uploader_key}", label_visibility="collapsed")
    
    st.markdown("---")

    st.subheader("🚀 Ações")
    # Botão Iniciar Auditoria
    if st.button("▶️ Auditar SP vs Listas", type="primary", use_container_width=True):
        st.session_state.start_audit_clicked = True
        st.session_state.start_extract_clicked = False # Garante que o outro está falso
        st.rerun() # Inicia a ação
    
    # --- NOVO BOTÃO DE EXTRAÇÃO ---
    if st.button("▶️ Extrair Lista de Equipamentos", use_container_width=True):
        st.session_state.start_audit_clicked = False # Garante que o outro está falso
        st.session_state.start_extract_clicked = True
        st.rerun() # Inicia a ação

    # Botão Limpar Tudo
    if st.button("🧹 Limpar Tudo", use_container_width=True):
         st.session_state.audit_results = None
         st.session_state.extract_results = None # NOVO
         st.session_state.read_error = None
         st.session_state.start_audit_clicked = False
         st.session_state.start_extract_clicked = False # NOVO
         # Incrementa as chaves para forçar o reset dos uploaders
         st.session_state.sp_file_uploader_key += 1
         st.session_state.lm_uploader_key += 1
         st.rerun() # Recarrega a página

# --- Área Principal (Resultados) ---
st.markdown('<div class="frame output-frame">', unsafe_allow_html=True) # Moldura única
st.header("📊 Status e Resultados")

# Lógica principal de execução (AUDITORIA)
if st.session_state.start_audit_clicked:
    st.session_state.read_error = None # Limpa antes de tentar ler
    st.session_state.audit_results = None # Limpa resultados antigos
    st.session_state.extract_results = None # Limpa o outro resultado

    # Validações
    valid = True
    if not google_api_key_from_secrets: 
        st.error("🔑 Chave API não configurada nos Segredos/Ambiente."); valid = False
    
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    current_lm_key = f"lm_uploader_{st.session_state.lm_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    analysis_files_obj = st.session_state.get(current_lm_key)
    
    if not sp_file_obj: st.error("📄 Arquivo SP não foi carregado."); valid = False
    if not analysis_files_obj: st.error("📊 Nenhuma Lista de Engenharia foi carregada."); valid = False

    if valid:
        try:
            # os.environ["GOOGLE_API_KEY"] = api_key_to_use # Não precisa, a lib lê
            
            # Leitura
            with st.spinner("⚙️ Lendo arquivos..."):
                sp_content = read_sp_file(sp_file_obj)
                analysis_content, file_names = read_analysis_files(analysis_files_obj)

            if st.session_state.read_error: st.error(st.session_state.read_error) # Exibe erro de leitura
            elif not sp_content or not analysis_content: st.warning("⚠️ Conteúdo de um ou mais arquivos parece vazio ou não pôde ser lido.")
            else:
                st.success(f"✅ Arquivos lidos!")
                MODEL_NAME = "gemini-flash-latest"
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME) # Chave lida do ambiente
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_AUDIT) # Usa o prompt de auditoria
                llm_chain = prompt_template | llm | StrOutputParser()

                # Execução
                with st.spinner(f"🧠 Auditando ({MODEL_NAME})... (Pode levar um tempo)"):
                    char_count = len(sp_content or "") + len(analysis_content or "")
                    st.info(f"📡 Enviando {char_count:,} caracteres para a API Gemini...")
                    raw_output = llm_chain.invoke({"sp_content": sp_content, "analysis_content": analysis_content})

                    # Processa e guarda resultados
                    report_markdown = raw_output; summary_data = pd.DataFrame()
                    summary_marker = "[RESUMO ESTRUTURADO PARA GRÁFICOS]"
                    if summary_marker in raw_output:
                        parts = raw_output.split(summary_marker, 1); report_markdown = parts[0].strip()
                        summary_section = parts[1].strip()
                        if summary_section and summary_section.lower().strip() != "nenhuma":
                            summary_data = parse_summary_table(summary_section)
                    st.success("🎉 Auditoria Concluída!")
                    st.session_state.audit_results = (summary_data, report_markdown) # Salva para exibição

        # Tratamento de Erros
        except Exception as e:
            error_message = f"❌ Erro durante a auditoria: {e}"
            if "API key" in str(e) or "credential" in str(e).lower(): error_message = f"🔑 Erro API Key: Verifique os Secrets. {e}"
            elif "quota" in str(e).lower() or "limit" in str(e).lower() or "free tier" in str(e).lower(): error_message = f"🚦 Limite da API Atingido: {e}"
            elif "model" in str(e).lower() and "not found" in str(e).lower(): error_message = f"🤷 Modelo não encontrado ('{MODEL_NAME}'). Verifique o nome."
            st.error(error_message); st.session_state.audit_results = None

    # Limpa o estado do botão DEPOIS de processar ou falhar
    st.session_state.start_audit_clicked = False
    if valid: st.rerun()

# --- NOVO BLOCO: Lógica principal de execução (EXTRAÇÃO) ---
elif st.session_state.start_extract_clicked:
    st.session_state.read_error = None # Limpa antes de tentar ler
    st.session_state.audit_results = None # Limpa o outro resultado
    st.session_state.extract_results = None # Limpa resultados antigos

    # Validações (só precisa da SP e da Chave)
    valid = True
    if not google_api_key_from_secrets: 
        st.error("🔑 Chave API não configurada nos Segredos/Ambiente."); valid = False
    
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    
    if not sp_file_obj: st.error("📄 Arquivo SP não foi carregado."); valid = False

    if valid:
        try:
            # Leitura (só SP)
            with st.spinner("⚙️ Lendo arquivo SP..."):
                sp_content = read_sp_file(sp_file_obj)

            if st.session_state.read_error: st.error(st.session_state.read_error) # Exibe erro de leitura
            elif not sp_content: st.warning("⚠️ Conteúdo do arquivo SP parece vazio.")
            else:
                st.success(f"✅ Arquivo SP lido!")
                MODEL_NAME = "gemini-flash-latest"
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME) # Chave lida do ambiente
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_EXTRACT) # Usa o prompt de extração
                llm_chain = prompt_template | llm | StrOutputParser()

                # Execução
                with st.spinner(f"🧠 Extraindo Lista Mestra ({MODEL_NAME})... (Pode levar um tempo)"):
                    char_count = len(sp_content or "")
                    st.info(f"📡 Enviando {char_count:,} caracteres para a API Gemini...")
                    # 'analysis_content' não é necessário para este prompt, mas o 'invoke' espera ele
                    # Vamos enviar um dict que só tem 'sp_content'
                    raw_output = llm_chain.invoke({"sp_content": sp_content}) 

                    # Processa e guarda resultados (sem gráfico)
                    report_markdown = raw_output.strip()
                    summary_data = pd.DataFrame() # Cria um DF vazio
                    st.success("🎉 Extração da Lista Mestra Concluída!")
                    st.session_state.extract_results = (summary_data, report_markdown) # Salva para exibição

        # Tratamento de Erros
        except Exception as e:
            error_message = f"❌ Erro durante a extração: {e}"
            if "API key" in str(e) or "credential" in str(e).lower(): error_message = f"🔑 Erro API Key: Verifique os Secrets. {e}"
            elif "quota" in str(e).lower() or "limit" in str(e).lower() or "free tier" in str(e).lower(): error_message = f"🚦 Limite da API Atingido: {e}"
            elif "model" in str(e).lower() and "not found" in str(e).lower(): error_message = f"🤷 Modelo não encontrado ('{MODEL_NAME}'). Verifique o nome."
            st.error(error_message); st.session_state.extract_results = None

    # Limpa o estado do botão DEPOIS de processar ou falhar
    st.session_state.start_extract_clicked = False
    if valid: st.rerun()


# --- Exibição de Resultados (Mostra o último que foi gerado) ---
# Encontra o resultado ativo (Auditoria ou Extração)
active_results = st.session_state.audit_results or st.session_state.extract_results
audit_type = None
if st.session_state.audit_results: audit_type = "Auditoria"
elif st.session_state.extract_results: audit_type = "Extração da SP"


if active_results:
    summary_data, report_markdown = active_results
    st.markdown(f"#### {audit_type}: Relatório Detalhado")

    # Botão de Download para o Relatório (sempre disponível se houver relatório)
    st.download_button(
         label=f"📄 Baixar Relatório ({audit_type})",
         data=report_markdown if report_markdown else "Nenhum relatório gerado.",
         file_name=f"relatorio_{audit_type.lower().replace(' ', '_')}_{time.strftime('%Y%m%d_%H%M%S')}.md", # Nome com data/hora
         mime='text/markdown',
     )
    with st.expander(f"Clique para ver os detalhes ({audit_type})", expanded=True): # Começa aberto
        st.markdown(report_markdown if report_markdown else f"*Nenhum relatório ({audit_type}) gerado.*")

    st.markdown("---") # Separador visual

    # ----- Exibe o Gráfico SOMENTE se for 'Auditoria' e tiver dados -----
    if audit_type == "Auditoria" and isinstance(summary_data, pd.DataFrame) and not summary_data.empty:
        st.markdown("#### Resumo Gráfico das Pendências")
        try:
            chart_data = summary_data.groupby(['Lista', 'Tipo']).size().reset_index(name='Contagem')

            # --- BOTÃO DOWNLOAD TABELA DE PENDÊNCIAS (CSV) ---
            csv_data = convert_df_to_csv(summary_data) # Converte todo o summary_data
            st.download_button(
                label="💾 Baixar Tabela de Pendências (CSV)",
                data=csv_data,
                file_name=f"pendencias_auditoria_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                mime='text/csv',
            )

            # ----- DIAGNÓSTICO (Removido da UI, mas pode ser re-adicionado) -----
            # with st.expander("Dados agregados usados para o gráfico (`chart_data`)"):
            #     st.dataframe(chart_data)

            # --- GRÁFICO COM EIXOS INVERTIDOS ---
            color_scale = alt.Scale(domain=['FALTANTE', 'DISCREPANCIA_TECNICA', 'DISCREPANCIA_QUANTIDADE', 'IMPLICITO_FALTANTE'],
                                    range=['#e45756', '#f58518', '#4c78a8', '#54a24b']) # Cores
            tooltip_config = ['Lista', 'Tipo', 'Contagem'] # Simplificado

            chart = alt.Chart(chart_data).mark_bar().encode(
                # Eixos Invertidos: Lista no Y, Contagem no X
                y=alt.Y('Lista', sort='-x', title='Lista / Origem'), # Ordena Lista pela Contagem
                x=alt.X('Contagem', title='Nº de Pendências'),
                color=alt.Color('Tipo', scale=color_scale, title='Tipo de Pendência'),
                tooltip=tooltip_config
            ).properties(
                title='Distribuição das Pendências por Lista e Tipo'
            ).interactive() # Habilita interatividade (zoom, pan, e menu de salvar)

            st.altair_chart(chart, use_container_width=True)
            st.caption("Passe o mouse sobre as barras para detalhes. Use o menu (⋮) no canto do gráfico para salvar como PNG/SVG.")

        except Exception as chart_error:
             st.error(f"⚠️ Erro ao gerar o gráfico: {chart_error}")
             # st.warning("Verifique a tabela 'chart_data' acima ou o formato do resumo estruturado no relatório detalhado.")

    # Condição se o relatório indica explicitamente 'nenhuma pendência' (para Auditoria)
    elif audit_type == "Auditoria" and (report_markdown and "nenhuma pendência encontrada" in report_markdown.lower()):
        st.info("✅ Nenhuma pendência foi encontrada na auditoria.")
    # Condição se summary_data está vazio E o relatório não diz 'nenhuma pendência' (para Auditoria)
    elif audit_type == "Auditoria":
         st.warning("⚠️ Não foi possível gerar o gráfico (dados de resumo ausentes ou inválidos). Verifique o relatório detalhado acima.")
    # Mensagem para 'Extração' (que não tem gráfico)
    elif audit_type == "Extração da SP":
        st.info("✅ Lista Mestra extraída com sucesso. Veja detalhes acima.")

# Mensagem inicial se nada foi processado ainda
elif (not st.session_state.start_audit_clicked and 
      not st.session_state.start_extract_clicked and 
      st.session_state.audit_results is None and 
      st.session_state.extract_results is None):
     st.info("Aguardando o upload dos arquivos e o início de uma auditoria...")


st.markdown('</div>', unsafe_allow_html=True) # Fecha moldura da área principal

# --- (Fim do código principal) ---




