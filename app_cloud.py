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
    """Lê múltiplos arquivos .csv ou .xlsx (Listas) e concatena em um único texto."""
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

# --- O Prompt Mestre (Reforçado) ---
MASTER_PROMPT = """
Sua **ÚNICA TAREFA** é comparar os itens físicos descritos na "Fonte da Verdade (SP)" (especificamente dos tópicos 17 ao 30) com os itens listados nas "Listas de Engenharia".

**NÃO GERE RELATÓRIOS DE KPIs, CPI, SPI, RAG status ou qualquer outra métrica de gerenciamento de projetos.** Foque **EXCLUSIVAMENTE** na comparação de itens físicos.

**SIGA ESTAS REGRAS ESTRITAMENTE:**
1.  **EXTRAÇÃO (SP):** Leia a SP (tópicos 17-30). Extraia itens físicos (comprados/fabricados). Um item existe se '[X] Sim' ou se houver especificação/descrição/notas.
2.  **COMPARAÇÃO (Listas):** Para cada item da SP, procure-o nas Listas de Engenharia. Verifique nome, quantidade e especificações técnicas relevantes. Use o NOME DO ARQUIVO da lista (ex: 'LME_200ELEL5477_REV02') ao reportar.
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

**DOCUMENTOS PARA ANÁLISE:**

[FONTE DA VERDADE (SP)]
{sp_content}
---
[LISTAS DE ENGENHARIA (Nomes dos arquivos incluídos no conteúdo)]
{analysis_content}
---

**INICIE O RELATÓRIO DE AUDITORIA DE PENDÊNCIAS ABAIXO:**
[RELATÓRIO DE AUDITORIA DE PENDÊNCIAS (Markdown)]

""" # Fim do Master Prompt Revisado

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
st.set_page_config(page_title="Agente Auditor v5", layout="wide")

# CSS para moldura (aplicada apenas na área principal agora)
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
    background-color: #ffd000; /* Cor de fundo levemente cinza para a sidebar */
}
</style>
"""
st.markdown(frame_css, unsafe_allow_html=True)

# --- Inicializa Session State ---
# 'hide_input_cols' Mantido caso queira reativar a funcionalidade
if 'hide_input_cols' not in st.session_state: st.session_state.hide_input_cols = False
if 'read_error' not in st.session_state: st.session_state.read_error = None
if 'audit_results' not in st.session_state: st.session_state.audit_results = None
if 'start_audit_clicked' not in st.session_state: st.session_state.start_audit_clicked = False
# Chaves para resetar uploaders
if 'sp_file_uploader_key' not in st.session_state: st.session_state.sp_file_uploader_key = 0
if 'lm_uploader_key' not in st.session_state: st.session_state.lm_uploader_key = 0


# --- Sidebar (Inputs e Ações - SEM CAMPO DE CHAVE) ---
with st.sidebar:
    # Adicionar um logo ou título na sidebar
    # st.image("URL_DA_SUA_LOGO.png", width=150) # Descomente se tiver um logo
    st.header("📄 Arquivos")
    st.markdown("###### Fonte da Verdade (SP)")
    # Usamos a chave de sessão para resetar o uploader no "Limpar"
    sp_file = st.file_uploader("Upload .docx", type=["docx"], key=f"sp_uploader_{st.session_state.sp_file_uploader_key}", label_visibility="collapsed")

    st.markdown("###### Listas de Engenharia")
    analysis_files = st.file_uploader("Upload .xlsx, .csv", type=["xlsx", "csv"],
                                      accept_multiple_files=True, key=f"lm_uploader_{st.session_state.lm_uploader_key}", label_visibility="collapsed")
    

    st.subheader("🚀 Ações")
    # Botão Iniciar Auditoria
    if st.button("▶️ Iniciar Auditoria", type="primary", use_container_width=True):
        st.session_state.start_audit_clicked = True
        # st.rerun() # Rerun é chamado na lógica principal agora

    # Botão Limpar Tudo
    if st.button("🧹 Limpar Tudo", use_container_width=True):
         st.session_state.audit_results = None
         st.session_state.read_error = None
         st.session_state.start_audit_clicked = False
         # Incrementa as chaves para forçar o reset dos uploaders
         st.session_state.sp_file_uploader_key += 1
         st.session_state.lm_uploader_key += 1
         st.rerun() # Recarrega a página

    st.markdown("---")

     # st.subheader("⚙️ Controles")

    # Apenas verifica e informa o status da chave (lida do ambiente/secrets)
    st.subheader("Status da Chave API")
    google_api_key_from_secrets = os.getenv("GOOGLE_API_KEY")
    if google_api_key_from_secrets:
        st.caption("🔒 Chave API configurada (via Segredos/Ambiente).")
    else:
        st.caption("⚠️ Chave API NÃO configurada nos Segredos/Ambiente.")
        st.caption("No Streamlit Cloud: vá em 'Settings > Secrets'.")
        st.caption("Localmente: defina a variável de ambiente GOOGLE_API_KEY.")


# --- Área Principal (Resultados) ---
st.markdown('<div class="frame output-frame">', unsafe_allow_html=True) # Moldura única
st.header("📊 Status e Resultados da Auditoria")

# Lógica principal de execução (roda se o botão foi clicado)
if st.session_state.start_audit_clicked:
    st.session_state.read_error = None # Limpa antes de tentar ler
    st.session_state.audit_results = None # Limpa resultados antigos

    # Validações
    valid = True
    # Verifica APENAS se a chave foi encontrada no ambiente/secrets
    if not google_api_key_from_secrets:
        st.error("🔑 Chave API não configurada nos Segredos/Ambiente."); valid = False
        # (Restante das validações de arquivos como antes)
        current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    # Pega os arquivos dos uploaders atuais
    # A chave dos uploaders muda no "Limpar", então pegamos pelo estado atual
    current_sp_key = f"sp_uploader_{st.session_state.sp_file_uploader_key}"
    current_lm_key = f"lm_uploader_{st.session_state.lm_uploader_key}"
    sp_file_obj = st.session_state.get(current_sp_key)
    analysis_files_obj = st.session_state.get(current_lm_key)
    if not sp_file_obj: st.error("📄 Arquivo SP não foi carregado."); valid = False
    if not analysis_files_obj: st.error("📊 Nenhuma Lista de Engenharia foi carregada."); valid = False

    if valid:
        try:
            # Configura a chave API para a sessão (importante se não usar secrets)

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
                prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT)
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
            if "API key" in str(e) or "credential" in str(e).lower(): error_message = f"🔑 Erro API Key: Verifique a chave inserida ou os Segredos. {e}"
            elif "quota" in str(e).lower() or "limit" in str(e).lower() or "free tier" in str(e).lower(): error_message = f"🚦 Limite da API Atingido: {e}"
            elif "model" in str(e).lower() and "not found" in str(e).lower(): error_message = f"🤷 Modelo não encontrado ('{MODEL_NAME}'). Verifique o nome."
            st.error(error_message); st.session_state.audit_results = None

    # Limpa o estado do botão DEPOIS de processar ou falhar, para evitar reruns indesejados
    st.session_state.start_audit_clicked = False
    # Força um rerun SE HOUVE SUCESSO OU ERRO para garantir a exibição correta dos resultados/mensagens
    if valid:
        st.rerun()


# Exibe os resultados (se existirem e o botão não foi clicado *agora*)
# Usamos a verificação do audit_results no session_state diretamente
if 'audit_results' in st.session_state and st.session_state.audit_results:
    summary_data, report_markdown = st.session_state.audit_results

    # ----- PASSO 1: EXIBIR O RELATÓRIO DETALHADO PRIMEIRO -----
    st.markdown("#### Relatório Detalhado")
    # Botão de Download para o Relatório (como texto simples)
    st.download_button(
         label="📄 Baixar Relatório (Texto)",
         data=report_markdown if report_markdown else "Nenhum relatório gerado.",
         file_name=f"auditoria_report_{time.strftime('%Y%m%d_%H%M%S')}.md", # Nome com data/hora
         mime='text/markdown',
     )
    with st.expander("Clique para ver os detalhes da auditoria", expanded=False): # Começa fechado
        st.markdown(report_markdown if report_markdown else "*Nenhum relatório em Markdown foi gerado ou encontrado.*")

    st.markdown("---") # Separador visual

    # ----- PASSO 2: TENTAR PROCESSAR E EXIBIR O GRÁFICO -----
    # Verifica se summary_data é um DataFrame e não está vazio
    if isinstance(summary_data, pd.DataFrame) and not summary_data.empty:
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

            # ----- DIAGNÓSTICO: MOSTRAR OS DADOS DO GRÁFICO -----
            with st.expander("Dados agregados usados para o gráfico (`chart_data`)"):
                st.dataframe(chart_data)

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
             st.warning("Verifique a tabela 'chart_data' acima ou o formato do resumo estruturado no relatório detalhado.")

    # Condição se o relatório indica explicitamente 'nenhuma pendência'
    elif report_markdown and "nenhuma pendência encontrada" in report_markdown.lower():
        st.info("✅ Nenhuma pendência foi encontrada na auditoria.")
    # Condição se summary_data está vazio E o relatório não diz 'nenhuma pendência'
    else:
         st.warning("⚠️ Não foi possível gerar o gráfico (dados de resumo ausentes ou inválidos). Verifique o relatório detalhado acima.")

# Mensagem inicial se nada foi processado ainda (nenhum resultado salvo e botão não clicado)
elif not st.session_state.start_audit_clicked and st.session_state.audit_results is None:
     st.info("Aguardando o upload dos arquivos e o início da auditoria...")


st.markdown('</div>', unsafe_allow_html=True) # Fecha moldura da área principal

# --- (Fim do código principal) ---
