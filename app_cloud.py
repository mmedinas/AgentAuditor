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

# --- Funções para Ler os Arquivos (Sem alteração) ---

def read_sp_file(file):
    """Lê o conteúdo de um arquivo .docx (SP) e retorna como texto."""
    try:
        document = docx.Document(file)
        full_text = [para.text for para in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    full_text.append(cell.text)
        return '\n'.join(full_text)
    except Exception as e:
        st.session_state.read_error = f"Erro ao ler SP ({file.name}): {e}"
        return ""

def read_analysis_files(files):
    """Lê múltiplos arquivos .csv ou .xlsx (Listas) e concatena."""
    all_content, file_names = [], []
    for file in files:
        try:
            content = ""
            file_base_name = os.path.splitext(file.name)[0] 
            if file.name.endswith('.csv'):
                df = pd.read_csv(BytesIO(file.getvalue()))
                content = df.to_string()
            elif file.name.endswith('.xlsx'):
                df = pd.read_excel(BytesIO(file.getvalue()))
                content = df.to_string()
            file_names.append(file_base_name)
            all_content.append(f"--- CONTEÚDO DO ARQUIVO: {file_base_name} ---\n{content}\n")
        except Exception as e:
            st.session_state.read_error = f"Erro ao ler Lista ({file.name}): {e}"
            return "", []
    return '\n'.join(all_content), file_names

# --- Prompt Mestre para Auditoria (Sem alteração) ---
MASTER_PROMPT_AUDIT = """
Sua **ÚNICA TAREFA** é comparar os itens físicos descritos na "Fonte da Verdade (SP)"...
... (Restante do prompt de auditoria como antes) ...
[RESUMO ESTRUTURADO PARA GRÁFICOS]
| TipoPendencia           | NomeLista                 | DetalheItem                                        |
... (como antes) ...
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

# --- PROMPT DE EXTRAÇÃO (ATUALIZADO PARA TABELA LARGA) ---
MASTER_PROMPT_EXTRACT = """
Sua **ÚNICA TAREFA** é atuar como um engenheiro de orçamentos e extrair uma **Lista Mestra de Equipamentos** (Bill of Materials - BOM) do documento "Fonte da Verdade (SP)".
**NÃO GERE RELATÓRIOS DE KPIs, CPI, SPI, etc.** Foque **EXCLUSIVAMENTE** na extração de itens físicos.
**REGRAS ESTRITAS:**
1.  **LEITURA COMPLETA:** Leia **TODO** o documento "FONTE DA VERDADE (SP)" (do início ao fim) para encontrar itens.
2.  **FONTES DE ITENS:** (Fonte A: Listas Finais, Fonte B: Texto Corrido).
3.  **CONSOLIDAÇÃO:** Crie uma **ÚNICA** lista mestra.
4.  **REMOVER DUPLICATAS:** Se um item da "Fonte B" (texto corrido) já estiver listado na "Fonte A" (listas finais), **NÃO** o repita.
5.  **RELATÓRIO DE EXTRAÇÃO:** Apresente a lista consolidada em formato Markdown. Tente agrupar por categoria.
**FORMATO OBRIGATÓRIO DO RELATÓRIO MARKDOWN:**
### Lista Mestra de Equipamentos (Extraída da SP)
#### Categoria: Elétricos
* [Item 1] (Qtd: [Qtd], Especificação: [Breve Espec.])
* [Item 2] (Qtd: [Qtd], Especificação: [Breve Espec.])
* (Continue para todas as categorias e itens encontrados)
---
**IMPORTANTE: APÓS o relatório Markdown, adicione a seção de resumo estruturado para EXPORTAÇÃO (FORMATO LARGO):**

[RESUMO ESTRUTURADO PARA EXTRAÇÃO]
| Categoria | Item_Consolidado | Quantidade | Especificacao_Resumida |
| :--- | :--- | :--- | :--- |
| Elétricos | Gerador | 1 | Diesel, silencioso (máx 67dB), regime Intermitente (Prime) |
| Mobiliário | Cadeira de Coleta | 5 | Movimento Trendelenburg, Capacidade 130-250kg |
* (Repita uma linha para CADA item consolidado. Use 'N/A' se a Qtd ou Espec. não for informada.)
* Se não houver itens, escreva "Nenhuma".
---
**DOCUMENTO PARA ANÁLISE (NÃO INVENTE DADOS SE ELE NÃO FOR FORNECIDO):**
--- INÍCIO DA FONTE DA VERDADE (SP) ---
{sp_content}
--- FIM DA FONTE DA VERDADE (SP) ---
**INICIE A LISTA MESTRA CONSOLIDADA ABAIXO:**
[LISTA MESTRA DE EQUIPAMENTOS (Markdown)]
"""


# --- Função para Parsear o Resumo Estruturado (Auditoria) ---
def parse_summary_table(summary_section):
    pendencias = []
    pattern = r"\|\s*(FALTANTE|DISCREPANCIA_TECNICA|DISCREPANCIA_QUANTIDADE|IMPLICITO_FALTANTE)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    lines = summary_section.strip().split('\n')
    if len(lines) > 2:
        data_lines = lines[2:]
        for line in data_lines:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                tipo = match.group(1).strip().upper()
                lista_raw = match.group(2).strip()
                detalhe = match.group(3).strip()
                if lista_raw.upper() == 'N/A':
                    lista_clean = 'Geral/Não Encontrado'
                else:
                    lista_base = os.path.basename(lista_raw); lista_clean = os.path.splitext(lista_base)[0]
                    base_name_match = re.match(r"([a-zA-Z]+)(_|\d|-|$)", lista_clean)
                    if base_name_match: lista_clean = base_name_match.group(1)
                    else: lista_clean = lista_raw
                pendencias.append({"Tipo": tipo, "Lista": lista_clean, "Item": detalhe})
    return pd.DataFrame(pendencias)

# --- FUNÇÃO DE PARSER ATUALIZADA (Para 4 colunas "Largas") ---
def parse_extract_table(summary_section):
    """Parseia a tabela estruturada 'wide' (4 colunas) da função de extração."""
    itens = []
    # Padrão para 4 colunas: Categoria | Item | Quantidade | Especificacao
    pattern = r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    lines = summary_section.strip().split('\n')
    if len(lines) > 2:
        data_lines = lines[2:] # Pula header e linha de separação
        for line in data_lines:
            match = re.search(pattern, line)
            # Garante que não é uma linha vazia ou de formatação
            if match and match.group(1).strip() != ":---":
                categoria = match.group(1).strip()
                item = match.group(2).strip()
                quantidade = match.group(3).strip()
                especificacao = match.group(4).strip()
                
                itens.append({
                    "Categoria": categoria, 
                    "Item_Consolidado": item, # Renomeado para clareza
                    "Quantidade": quantidade, 
                    "Especificacao_Resumida": especificacao
                })
    return pd.DataFrame(itens)


# --- FUNÇÃO DE CONVERSÃO CSV (ATUALIZADA) ---
@st.cache_data
def convert_df_to_csv(df):
    """Converte DataFrame para CSV usando ; como separador para Excel (PT-BR)."""
    if df is None or df.empty:
        return "".encode('utf-8')
    # Usa sep=';' para Excel (Brasil/Europa) e utf-8-sig para incluir BOM (Byte Order Mark)
    return df.to_csv(index=False, sep=';').encode('utf-8-sig')

# --- Configuração da Página e CSS (Sem alteração) ---
st.set_page_config(page_title="Agente Auditor v6.3", layout="wide")

frame_css = """
<style>
/* Estilo base da moldura */
.frame {
    border: 1px solid #e1e4e8; border-radius: 6px; padding: 1rem;
    background-color: #f6f8fa; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    margin-bottom: 1rem; min-height: 400px; /* Altura mínima para a área de resultados */
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
    background-color: #F8F9FA;
}
</style>
"""
st.markdown(frame_css, unsafe_allow_html=True)

# --- Inicializa Session State (Sem alteração) ---
if 'read_error' not in st.session_state: st.session_state.read_error = None
if 'audit_results' not in st.session_state: st.session_state.audit_results = None
if 'start_audit_clicked' not in st.session_state: st.session_state.start_audit_clicked = False
if 'extract_results' not in st.session_state: st.session_state.extract_results = None 
if 'start_extract_clicked' not in st.session_state: st.session_state.start_extract_clicked = False 
if 'sp_file_uploader_key' not in st.session_state: st.session_state.sp_file_uploader_key = 0
if 'lm_uploader_key' not in st.session_state: st.session_state.lm_uploader_key = 0


# --- Header (Sem alteração) ---
st.title("🤖✨ Agente Auditor v6.3")
st.caption("Auditoria SP vs. Listas & Extração de Lista Mestra | Gemini Cloud")


# --- Sidebar (Sem alteração) ---
with st.sidebar:
    st.image("https://raw.githubusercontent.com/mmedinas/AgentAuditor/main/LOGO_MOBILE.png", width=150)
    st.header("⚙️ Controles")
    
    st.subheader("Chave API")
    google_api_key_from_secrets = os.getenv("GOOGLE_API_KEY")
    if google_api_key_from_secrets:
         st.caption("🔒 Chave API configurada (via Segredos/Ambiente).")
    else:
         st.caption("⚠️ Chave API NÃO configurada nos Segredos/Ambiente.")
         st.caption("No Streamlit Cloud: vá em 'Settings > Secrets'.")
         st.caption("Localmente: defina a variável de ambiente GOOGLE_API_KEY.")

    st.markdown("---")
    
    st.subheader("📄 Arquivos")
    st.markdown("###### Documento de Entrada (SP)")
    sp_file = st.file_uploader("Upload .docx", type=["docx"], key=f"sp_uploader_{st.session_state.sp_file_uploader_key}", label_visibility="collapsed")

    st.markdown("###### Listas de Engenharia (LMM, LME, LMH)")
    analysis_files = st.file_uploader("Upload .xlsx, .csv", type=["xlsx", "csv"],
                                      accept_multiple_files=True, key=f"lm_uploader_{st.session_state.lm_uploader_key}", label_visibility="collapsed")
    
    st.markdown("---")

    st.subheader("🚀 Ações")
    if st.button("▶️ Auditar SP vs Listas", type="primary", use_container_width=True):
        st.session_state.start_audit_clicked = True
        st.session_state.start_extract_clicked = False 
        st.rerun() 
    if st.button("▶️ Extrair Lista Mestra da SP", use_container_width=True):
        st.session_state.start_audit_clicked = False 
        st.session_state.start_extract_clicked = True
        st.rerun() 
    if st.button("🧹 Limpar Tudo", use_container_width=True):
         st.session_state.audit_results = None; st.session_state.extract_results = None
         st.session_state.read_error = None
         st.session_state.start_audit_clicked = False; st.session_state.start_extract_clicked = False
         st.session_state.sp_file_uploader_key += 1; st.session_state.lm_uploader_key += 1
         st.rerun() 

# --- Área Principal (Resultados) ---
# st.markdown('<div class="frame output-frame">', unsafe_allow_html=True) # Moldura (comentada)
st.header("📊 Status e Resultados da Auditoria")

# Lógica principal de execução (AUDITORIA - ATUALIZADA com temperature=0.0)
if st.session_state.start_audit_clicked:
    st.session_state.read_error = None; st.session_state.audit_results = None; st.session_state.extract_results = None
    
    valid = True
    if not google_api_key_from_secrets: st.error("🔑 Chave API?"); valid = False
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    current_lm_key = f"lm_uploader_{st.session_state.lm_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    analysis_files_obj = st.session_state.get(current_lm_key)
    if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False
    if not analysis_files_obj: st.error("📊 Listas Eng.?"); valid = False

    if valid:
        try:
            with st.spinner("⚙️ Lendo arquivos..."):
                sp_content = read_sp_file(sp_file_obj)
                analysis_content, file_names = read_analysis_files(analysis_files_obj)
            if st.session_state.read_error: st.error(st.session_state.read_error)
            elif not sp_content or not analysis_content: st.warning("⚠️ Conteúdo vazio.")
            else:
                st.success(f"✅ Arquivos lidos!")
                MODEL_NAME = "gemini-flash-latest"
                # --- TRAVA DE CONSISTÊNCIA ---
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0.0) 
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_AUDIT) 
                llm_chain = prompt_template | llm | StrOutputParser()
                with st.spinner(f"🧠 Auditando ({MODEL_NAME})..."):
                    char_count = len(sp_content or "") + len(analysis_content or "")
                    st.info(f"📡 Enviando {char_count:,} caracteres para a API Gemini...")
                    raw_output = llm_chain.invoke({"sp_content": sp_content, "analysis_content": analysis_content})
                    report_markdown = raw_output; summary_data = pd.DataFrame()
                    summary_marker = "[RESUMO ESTRUTURADO PARA GRÁFICOS]"
                    if summary_marker in raw_output:
                        parts = raw_output.split(summary_marker, 1); report_markdown = parts[0].strip()
                        summary_section = parts[1].strip()
                        if summary_section and summary_section.lower().strip() != "nenhuma":
                            summary_data = parse_summary_table(summary_section)
                    st.success("🎉 Auditoria Concluída!")
                    st.session_state.audit_results = (summary_data, report_markdown)
        except Exception as e:
            error_message = f"❌ Erro: {e}"; ... ; st.error(error_message);
    st.session_state.start_audit_clicked = False
    if valid: st.rerun()

# Lógica de (EXTRAÇÃO) ATUALIZADA (com temperature=0.0)
elif st.session_state.start_extract_clicked:
    st.session_state.read_error = None; st.session_state.audit_results = None; st.session_state.extract_results = None
    
    valid = True
    if not google_api_key_from_secrets: st.error("🔑 Chave API?"); valid = False
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False

    if valid:
        try:
            with st.spinner("⚙️ Lendo arquivo SP..."):
                sp_content = read_sp_file(sp_file_obj)
            if st.session_state.read_error: st.error(st.session_state.read_error)
            elif not sp_content: st.warning("⚠️ Conteúdo da SP vazio.")
            else:
                st.success(f"✅ Arquivo SP lido!")
                MODEL_NAME = "gemini-flash-latest"
                # --- TRAVA DE CONSISTÊNCIA ---
                llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0.0) 
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT_EXTRACT) 
                llm_chain = prompt_template | llm | StrOutputParser()
                with st.spinner(f"🧠 Extraindo Lista Mestra ({MODEL_NAME})..."):
                    char_count = len(sp_content or "")
                    st.info(f"📡 Enviando {char_count:,} caracteres para a API Gemini...")
                    raw_output = llm_chain.invoke({"sp_content": sp_content}) 
                    
                    report_markdown = raw_output; summary_data = pd.DataFrame() 
                    summary_marker = "[RESUMO ESTRUTURADO PARA EXTRAÇÃO]" 
                    
                    if summary_marker in raw_output:
                        parts = raw_output.split(summary_marker, 1); report_markdown = parts[0].strip()
                        summary_section = parts[1].strip()
                        if summary_section and summary_section.lower().strip() != "nenhuma":
                            summary_data = parse_extract_table(summary_section) # <-- Usa parser de 4 colunas
                    
                    st.success("🎉 Extração da Lista Mestra Concluída!")
                    st.session_state.extract_results = (summary_data, report_markdown) 
        except Exception as e:
            error_message = f"❌ Erro: {e}"; ... ; st.error(error_message);
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
         label=f"📄 Baixar Relatório ({audit_type}) (Markdown)",
         data=report_markdown if report_markdown else "Nenhum relatório gerado.",
         file_name=f"relatorio_{audit_type.lower().replace(' ', '_')}_{time.strftime('%Y%m%d_%H%M%S')}.md",
         mime='text/markdown',
     )
    
    # --- BOTÃO DE DOWNLOAD CSV (CONDICIONAL) ---
    # Mostra se tivermos um summary_data (seja da Auditoria ou Extração)
    if isinstance(summary_data, pd.DataFrame) and not summary_data.empty:
        csv_data = convert_df_to_csv(summary_data)
        file_name_prefix = "pendencias_auditoria" if audit_type == "Auditoria" else "lista_mestra_extracao"
        st.download_button(
            label=f"💾 Baixar Tabela ({audit_type}) (CSV)", # Label dinâmica
            data=csv_data,
            file_name=f"{file_name_prefix}_{time.strftime('%Y%m%d_%H%M%S')}.csv",
            mime='text/csv',
        )
    # --- (NOVO) Feedback se o CSV não for gerado na Extração ---
    elif audit_type == "Extração da SP":
        st.warning("⚠️ **Aviso:** A IA gerou o relatório em Markdown, mas falhou em fornecer os dados estruturados para o arquivo CSV.")


    # Expander para o relatório de texto
    with st.expander(f"Clique para ver os detalhes ({audit_type})", expanded=True): # Começa aberto
        st.markdown(report_markdown if report_markdown else f"*Nenhum relatório ({audit_type}) gerado.*")

    st.markdown("---") # Separador visual

    # ----- Exibe o Gráfico SOMENTE se for 'Auditoria' e tiver dados -----
    if audit_type == "Auditoria" and isinstance(summary_data, pd.DataFrame) and not summary_data.empty:
        st.markdown("#### Resumo Gráfico das Pendências")
        try:
            chart_data = summary_data.groupby(['Lista', 'Tipo']).size().reset_index(name='Contagem')

            # --- GRÁFICO ---
            color_scale = alt.Scale(domain=['FALTANTE', 'DISCREPANCIA_TECNICA', 'DISCREPANCIA_QUANTIDADE', 'IMPLICITO_FALTANTE'],
                                    range=['#e45756', '#f58518', '#4c78a8', '#54a24b']) 
            tooltip_config = ['Lista', 'Tipo', 'Contagem'] 

            chart = alt.Chart(chart_data).mark_bar().encode(
                y=alt.Y('Lista', sort='-x', title='Lista / Origem'),
                x=alt.X('Contagem', title='Nº de Pendências'),
                color=alt.Color('Tipo', scale=color_scale, title='Tipo de Pendência'),
                tooltip=tooltip_config
            ).properties(
                title='Distribuição das Pendências por Lista e Tipo'
            ).interactive()
            st.altair_chart(chart, use_container_width=True)
            st.caption("Use o menu (⋮) no canto do gráfico para salvar como PNG/SVG.")
        except Exception as chart_error:
             st.error(f"⚠️ Erro ao gerar o gráfico: {chart_error}")
    
    elif audit_type == "Auditoria":
         if report_markdown and "nenhuma pendência encontrada" in report_markdown.lower(): st.info("✅ Nenhuma pendência encontrada (Auditoria).")
         else: st.warning("⚠️ Gráfico não gerado (dados de resumo ausentes/inválidos para Auditoria).")
    
    # --- EXIBIÇÃO PARA EXTRAÇÃO (ATUALIZADA) ---
    elif audit_type == "Extração da SP":
        # A mensagem de aviso sobre o CSV já apareceu acima (se necessário)
        st.info("✅ Lista Mestra extraída. Veja o relatório acima.")
        if isinstance(summary_data, pd.DataFrame) and not summary_data.empty:
             with st.expander("Visualizar Tabela de Extração (Dados do CSV)"):
                st.dataframe(summary_data) # Mostra a tabela "larga"

# Mensagem inicial se nada foi processado ainda
elif (not st.session_state.start_audit_clicked and 
      not st.session_state.start_extract_clicked and 
      st.session_state.audit_results is None and 
      st.session_state.extract_results is None):
     st.info("Aguardando o upload dos arquivos e o início de uma auditoria...")

# st.markdown('</div>', unsafe_allow_html=True) # Moldura (comentada)

# --- (Fim do código principal) ---

