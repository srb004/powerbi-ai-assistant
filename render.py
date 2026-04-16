import json
import re

import pandas as pd
import streamlit as st


def clean_math_notation(text):
    text = re.sub(r'\$\$(.+?)\$\$', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.+?)\\\]', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\\((.+?)\\\)', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\$([A-Za-z][^\$]*?)\$', r'\1', text)
    text = re.sub(r'\\(?:text|mathrm|mathbf|textbf)\{([^}]+)\}', r'\1', text)
    text = re.sub(r'\\([A-Za-z])', r'\1', text)
    return text

def parse_response(content):
    content = clean_math_notation(content)

    parts = {
        "narrative_before": "",
        "table_rows": None,
        "narrative_after": "",
        "follow_ups": [],
    }

    follow_up_lines = []
    main_content = []
    for line in content.splitlines():
        if line.startswith("FOLLOW_UP:"):
            follow_up_lines.append(line.replace("FOLLOW_UP:", "").strip())
        else:
            main_content.append(line)

    parts["follow_ups"] = follow_up_lines
    content_clean = "\n".join(main_content)

    if "RENDER_TABLE:" in content_clean:
        sections = content_clean.split("RENDER_TABLE:", 1)
        parts["narrative_before"] = sections[0].strip()
        remainder = sections[1].strip()

        json_end = 0
        bracket_count = 0
        in_json = False
        for i, ch in enumerate(remainder):
            if ch == "[":
                in_json = True
                bracket_count += 1
            elif ch == "]":
                bracket_count -= 1
                if in_json and bracket_count == 0:
                    json_end = i + 1
                    break

        if json_end:
            try:
                parts["table_rows"] = json.loads(remainder[:json_end])
                parts["narrative_after"] = remainder[json_end:].strip()
            except json.JSONDecodeError:
                parts["narrative_before"] = content_clean
    else:
        parts["narrative_before"] = content_clean.strip()

    return parts


def clean_column_names(df):
    new_cols = []
    for col in df.columns:
        if "]." in col:
            col = col.split("].")[-1]
        col = col.strip("[]").replace("_", " ").title()
        new_cols.append(col)
    df.columns = new_cols
    return df


def render_response(content):
    parsed = parse_response(content)

    if parsed["narrative_before"]:
        st.markdown(parsed["narrative_before"])

    if parsed["table_rows"]:
        try:
            df = pd.DataFrame(parsed["table_rows"])
            df = clean_column_names(df)
            st.dataframe(df, use_container_width=True, hide_index=True)

            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            if numeric_cols and 1 < len(df) <= 30:
                with st.expander("View as chart"):
                    chart_col = st.selectbox(
                        "Metric",
                        numeric_cols,
                        key=f"chart_col_{abs(hash(content))}",
                    )
                    label_col = df.columns[0]
                    chart_df = df.set_index(label_col)[[chart_col]]
                    tab_bar, tab_line = st.tabs(["Bar", "Line"])
                    with tab_bar:
                        st.bar_chart(chart_df)
                    with tab_line:
                        st.line_chart(chart_df)

        except Exception:
            st.code(str(parsed["table_rows"]))

    if parsed["narrative_after"]:
        st.markdown(parsed["narrative_after"])

    if parsed["follow_ups"]:
        st.markdown("**You might also want to ask:**")
        cols = st.columns(len(parsed["follow_ups"]))
        for i, question in enumerate(parsed["follow_ups"]):
            with cols[i]:
                key = f"followup_{abs(hash(question))}_{i}_{abs(hash(content))}"
                if st.button(question, key=key, use_container_width=True):
                    st.session_state.pending_prompt = question
                    st.rerun()