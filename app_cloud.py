# -*- coding: utf-8 -*-
import streamlit as st
import os
import pandas as pd
import docx # pip install python-docx
from io import BytesIO
import re # Para extrair dados do resumo
import altair as alt # Para os gráficos

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
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    full_text.append(cell.text)
        return '\n'.join(full_text)
    except Exception as e:
        st.session_state.read_error = f"Erro ao ler SP ({file.name}): {e}"
        return ""

def read_analysis_files(files):
    """Lê múltiplos arquivos .csv ou .xlsx (Listas) e concatena em um único texto."""
    all_content, file_names = [], []
    for file in files:
        try:
            content = ""
            # Usa o nome base sem extensão para referência interna
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
    # Regex ajustado para nome da lista e detalhe
    pattern = r"\|\s*(FALTANTE|DISCREPANCIA_TECNICA|DISCREPANCIA_QUANTIDADE|IMPLICITO_FALTANTE)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    lines = summary_section.strip().split('\n')
    if len(lines) > 2:
        data_lines = lines[2:] # Pula header e linha de separação ----
        for line in data_lines:
            match = re.search(pattern, line, re.IGNORECASE)
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


# --- Configuração da Página e CSS ---
st.set_page_config(page_title="Agente Auditor v4", layout="wide")

# CSS para molduras (sem height: 100%)
frame_css = """
<style>
.frame {
    border: 1px solid #e1e4e8;
    border-radius: 6px;
    padding: 1rem;
    background-color: #f6f8fa;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    margin-bottom: 1rem;
}
.frame h3, .frame h5 {
    margin-top: 0;
    margin-bottom: 0.8rem;
    color: #0366d6;
    border-bottom: 1px solid #eaecef;
    padding-bottom: 0.3rem;
}
.stVerticalBlock > div:has(> .frame) {
     min-height: 150px; /* Altura mínima para colunas de input/ações */
}
.output-frame {
     min-height: 300px; /* Altura mínima maior para a área de resultados */
}
</style>
"""
st.markdown(frame_css, unsafe_allow_html=True)

# --- Inicializa Session State ---
if 'hide_input_cols' not in st.session_state: st.session_state.hide_input_cols = False
if 'read_error' not in st.session_state: st.session_state.read_error = None
if 'audit_results' not in st.session_state: st.session_state.audit_results = None
# Flag para controlar se a auditoria foi iniciada nesta execução
if 'start_audit_clicked' not in st.session_state: st.session_state.start_audit_clicked = False


# --- Header ---
st.markdown('<div class="frame">', unsafe_allow_html=True)
st.title("🤖✨ Agente Auditor de Projetos v4")
st.caption("Auditoria SP vs. Listas de Engenharia | Gemini Cloud")
st.markdown('</div>', unsafe_allow_html=True)


# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ Configuração")
    # Tenta ler a chave dos Secrets (ambiente). NÃO MOSTRA O CAMPO DE TEXTO.
    google_api_key_from_secrets = os.getenv("GOOGLE_API_KEY")

    if google_api_key_from_secrets:
        st.success("🔑 Chave API encontrada nos Segredos!")
    else:
        st.warning("🔑 Chave API não encontrada nos Segredos/Ambiente.")
        st.info("Configure GOOGLE_API_KEY em 'Settings > Secrets' no Streamlit Cloud.")

    st.markdown("---")
    st.header("👁️ Visualização")
    button_label = "Expandir Resultados" if not st.session_state.hide_input_cols else "Mostrar Inputs"
    if st.button(button_label, use_container_width=True):
        st.session_state.hide_input_cols = not st.session_state.hide_input_cols
        st.rerun()
    st.markdown("---")


# --- Função para Exibir Resultados (com ordem corrigida e diagnóstico) ---
def display_results():
    if 'audit_results' in st.session_state and st.session_state.audit_results:
        summary_data, report_markdown = st.session_state.audit_results

        # ----- PASSO 1: EXIBIR O RELATÓRIO DETALHADO PRIMEIRO -----
        st.markdown("#### Relatório Detalhado")
        with st.expander("Clique para ver os detalhes da auditoria", expanded=st.session_state.hide_input_cols):
            st.markdown(report_markdown if report_markdown else "*Nenhum relatório em Markdown foi gerado ou encontrado.*")

        st.markdown("---") # Separador visual

        # ----- PASSO 2: TENTAR PROCESSAR E EXIBIR O GRÁFICO -----
        if not summary_data.empty:
            st.markdown("#### Resumo Gráfico das Pendências")
            
            try: # Try/except robusto em volta de TUDO relacionado ao gráfico
                chart_data = summary_data.groupby(['Lista', 'Tipo']).size().reset_index(name='Contagem')

                # ----- DIAGNÓSTICO: MOSTRAR OS DADOS DO GRÁFICO -----
                with st.expander("Dados usados para o gráfico (`chart_data`)"):
                    st.dataframe(chart_data)
                # ---------------------------------------------------

                color_scale = alt.Scale(domain=['FALTANTE', 'DISCREPANCIA_TECNICA', 'DISCREPANCIA_QUANTIDADE', 'IMPLICITO_FALTANTE'],
                                        range=['#e45756', '#f58518', '#4c78a8', '#54a24b'])

                # ----- DIAGNÓSTICO: TOOLTIP SIMPLIFICADO -----
                tooltip_config = ['Lista', 'Tipo', 'Contagem']
                # tooltip_config = ['Lista', 'Tipo', 'Contagem', alt.Tooltip('Item', title='Exemplo Item')] # Original
                # -----------------------------------------------

                chart = alt.Chart(chart_data).mark_bar().encode(
                    x=alt.X('Lista', sort='-y', title='Lista / Origem'),
                    y=alt.Y('Contagem', title='Nº de Pendências'),
                    color=alt.Color('Tipo', scale=color_scale, title='Tipo de Pendência'),
                    tooltip=tooltip_config
                ).properties(
                    title='Distribuição das Pendências por Lista e Tipo'
                ).interactive()
                st.altair_chart(chart, use_container_width=True)

            except Exception as chart_error: # Captura qualquer erro do Altair/Pandas
                 st.error(f"⚠️ Erro ao gerar o gráfico: {chart_error}")
                 st.warning("Verifique a tabela 'chart_data' acima ou o formato do resumo estruturado no relatório detalhado.")

        elif report_markdown and "nenhuma pendência encontrada" in report_markdown.lower():
            st.info("✅ Nenhuma pendência foi encontrada na auditoria (confirmado pelo relatório).")
        else:
             st.warning("⚠️ Não foi possível gerar o gráfico (dados de resumo ausentes ou inválidos). Verifique o relatório detalhado acima.")

    # Mensagem inicial se nada foi processado ainda
    elif not st.session_state.start_audit_clicked:
         st.info("Aguardando o upload dos arquivos e o início da auditoria...")


# --- Layout Principal Condicional ---
if not st.session_state.hide_input_cols:
    # --- VISÃO PADRÃO (3 COLUNAS) ---
    col1, col2, col3 = st.columns([2, 1, 3]) # uploads(2), ações(1), resultados(3)

    with col1:
        st.markdown('<div class="frame">', unsafe_allow_html=True)
        st.subheader("📄 Arquivos")
        st.markdown("##### Fonte da Verdade (SP)")
        sp_file = st.file_uploader("Upload .docx", type=["docx"], key="sp_uploader_visible", label_visibility="collapsed")
        st.markdown("##### Listas de Engenharia")
        analysis_files = st.file_uploader("Upload .xlsx, .csv", type=["xlsx", "csv"], 
                                          accept_multiple_files=True, key="lm_uploader_visible", label_visibility="collapsed")
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="frame">', unsafe_allow_html=True)
        st.subheader("🚀 Ações")
        # Botão Iniciar Auditoria
        if st.button("Iniciar Auditoria", type="primary", use_container_width=True, key="start_button_visible"):
            st.session_state.start_audit_clicked = True # Marca que o botão foi clicado
            st.rerun() # Força rerun para entrar na lógica de processamento na col3
        
        # Botão Limpar Tudo
        if st.button("Limpar Tudo", use_container_width=True, key="clear_button_visible"):
             st.session_state.audit_results = None
             st.session_state.read_error = None
             st.session_state.start_audit_clicked = False # Reseta o estado do botão
             # Limpar uploaders é complexo, rerun geralmente é suficiente visualmente
             st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with col3:
        st.markdown('<div class="frame output-frame">', unsafe_allow_html=True) # Usa classe específica
        st.subheader("📊 Status e Resultados")

        # Lógica de execução da auditoria (só roda se o botão foi clicado *nesta* execução)
        if st.session_state.start_audit_clicked:
            st.session_state.read_error = None # Limpa antes de tentar ler
            st.session_state.audit_results = None # Limpa resultados antigos

            # Validações
            valid = True
            if not google_api_key_from_secrets: st.error("🔑 Chave API?"); valid = False
            sp_file_obj = st.session_state.get('sp_uploader_visible')
            analysis_files_obj = st.session_state.get('lm_uploader_visible')
            if not sp_file_obj: st.error("📄 Arquivo SP?"); valid = False
            if not analysis_files_obj: st.error("📊 Listas Eng.?"); valid = False
                
            if valid:
                try:
                    # Leitura
                    with st.spinner("⚙️ Lendo..."):
                        sp_content = read_sp_file(sp_file_obj)
                        analysis_content, file_names = read_analysis_files(analysis_files_obj)
                    
                    if st.session_state.read_error: st.error(st.session_state.read_error)
                    elif not sp_content or not analysis_content: st.warning("⚠️ Conteúdo vazio.")
                    else:
                        st.success(f"✅ Arquivos lidos!")
                        MODEL_NAME = "gemini-flash-latest" 
                        llm = ChatGoogleGenerativeAI(model=MODEL_NAME) # Chave lida do ambiente
                        prompt_template = ChatPromptTemplate.from_template(MASTER_PROMPT)
                        llm_chain = prompt_template | llm | StrOutputParser()

                        # Execução
                        with st.spinner(f"🧠 Auditando ({MODEL_NAME})..."):
                            char_count = len(sp_content or "") + len(analysis_content or "")
                            st.info(f"📡 Enviando {char_count:,} chars...")
                            raw_output = llm_chain.invoke({"sp_content": sp_content, "analysis_content": analysis_content})

                            # Processa e guarda resultados
                            report_markdown = raw_output; summary_data = pd.DataFrame()
                            summary_marker = "[RESUMO ESTRUTURADO PARA GRÁFICOS]"
                            if summary_marker in raw_output:
                                parts = raw_output.split(summary_marker, 1); report_markdown = parts[0].strip()
                                summary_section = parts[1].strip()
                                if summary_section and summary_section.lower() != "nenhuma":
                                    summary_data = parse_summary_table(summary_section)
                            st.success("🎉 Auditoria Concluída!")
                            st.session_state.audit_results = (summary_data, report_markdown)

                # Tratamento de Erros
                except Exception as e:
                    error_message = f"❌ Erro: {e}"
                    if "API key" in str(e) or "credential" in str(e).lower(): error_message = f"🔑 Erro API Key: Verifique os Secrets. {e}"
                    elif "quota" in str(e).lower() or "limit" in str(e).lower(): error_message = f"🚦 Limite API: {e}"
                    elif "model" in str(e).lower() and "not found" in str(e).lower(): error_message = f"🤷 Modelo não encontrado ('{MODEL_NAME}')."
                    st.error(error_message); st.session_state.audit_results = None 
            
            # Limpa o estado do botão DEPOIS de processar
            st.session_state.start_audit_clicked = False 
            st.rerun() # Força um rerun para exibir os resultados agora usando display_results()

        # Chama a função para exibir os resultados (se houver e o botão não acabou de ser clicado)
        else:
            display_results()

        st.markdown('</div>', unsafe_allow_html=True) # Fecha moldura col3

else:
    # --- VISÃO EXPANDIDA (APENAS RESULTADOS) ---
    st.markdown('<div class="frame output-frame">', unsafe_allow_html=True) # Usa classe específica
    st.subheader("📊 Resultados da Auditoria (Visão Expandida)")
    display_results() # Exibe os resultados guardados no session_state
    st.markdown
