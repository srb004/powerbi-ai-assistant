import json
import re
import pandas as pd
import streamlit as st

def parse_response(content: str) -> dict:
    parts = {
        "narrative_before": "",
        "table_rows": None,
        "narrative_after": "",
        "follow_ups": [],
    }

    follow_up_lines: list[str] = []
    main_lines: list[str] = []
    for line in content.splitlines():
        if line.strip().startswith("FOLLOW_UP:"):
            follow_up_lines.append(line.replace("FOLLOW_UP:", "").strip())
        else:
            main_lines.append(line)

    parts["follow_ups"] = follow_up_lines
    content_clean = "\n".join(main_lines)

    if "RENDER_TABLE:" not in content_clean:
        parts["narrative_before"] = _clean_narrative(content_clean)
        return parts

    before, remainder = content_clean.split("RENDER_TABLE:", 1)
    parts["narrative_before"] = _clean_narrative(before)
    remainder = remainder.strip()

    start = remainder.find("[")
    if start == -1:
        parts["narrative_before"] = _clean_narrative(content_clean)
        return parts

    bracket_count = 0
    end = -1
    for i, ch in enumerate(remainder[start:], start):
        if ch == "[":
            bracket_count += 1
        elif ch == "]":
            bracket_count -= 1
            if bracket_count == 0:
                end = i + 1
                break

    if end == -1:
        parts["narrative_before"] = _clean_narrative(content_clean)
        return parts

    json_str = remainder[start:end]
    after_json = remainder[end:].strip()

    try:
        parts["table_rows"] = json.loads(json_str)
    except json.JSONDecodeError:
        try:
            parts["table_rows"] = json.loads(_fix_json(json_str))
        except Exception:
            parts["narrative_before"] = _clean_narrative(content_clean)
            return parts

    after_clean = _strip_residual_json(after_json)
    parts["narrative_after"] = _clean_narrative(after_clean)
    return parts


def _strip_residual_json(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"^\s*\[[\s\S]*?\]\s*", "", text, count=1)
    text = re.sub(r"^\s*\{[\s\S]*?\}\s*", "", text, count=1)
    return text.strip()


def _clean_narrative(text: str) -> str:
    text = text.strip()
    text = re.sub(r"```[a-z]*\n?", "", text)
    text = text.replace("```", "")
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fix_json(s: str) -> str:
    s = re.sub(r"(?<!\w)'([^']*)'(?!\w)", r'"\1"', s)
    return s


def _is_residual_json(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        return True
    json_chars = set('[]{}":,0123456789 \n\t')
    non_json = sum(1 for c in stripped if c not in json_chars)
    if len(stripped) > 10 and non_json / len(stripped) < 0.15:
        return True
    return False

def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:

    new_cols: list[str] = []
    for col in df.columns:
        col = re.sub(r"^['\w\s]+\[", "", col)  
        col = col.strip("[]'\"")
        col = col.replace("_", " ").title()
        new_cols.append(col)
    df.columns = new_cols
    return df

def _format_number(val):
    try:
        f = float(val)
        if abs(f) >= 1_000_000:
            return f"{f / 1_000_000:.2f}M"
        if abs(f) >= 1_000:
            return f"{f / 1_000:.1f}K"
        if -10 < f < 10 and f != int(f):
            return f"{f:.4f}"
        if f != int(f):
            return f"{f:,.2f}"
        return f"{int(f):,}"
    except (TypeError, ValueError):
        return val

def render_response(content: str) -> None:
    parsed = parse_response(content)

    if parsed["narrative_before"]:
        st.markdown(parsed["narrative_before"])

    if parsed["table_rows"]:
        try:
            df = pd.DataFrame(parsed["table_rows"])

            df = df.loc[:, ~df.columns.str.match(r"^(index|Unnamed)")]

            df = clean_column_names(df)

            display_df = df.copy()
            for col in display_df.select_dtypes(include="number").columns:
                display_df[col] = display_df[col].apply(_format_number)

            st.dataframe(display_df, use_container_width=True, hide_index=True)

            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            non_numeric_cols = df.select_dtypes(exclude="number").columns.tolist()

            if numeric_cols and non_numeric_cols and 1 < len(df) <= 30:
                with st.expander("View as chart"):
                    chart_col = st.selectbox(
                        "Metric",
                        numeric_cols,
                        key=f"chart_col_{abs(hash(content))}",
                    )

                    label_col = non_numeric_cols[0]
                    chart_df = df[[label_col, chart_col]].copy()
                    chart_df[chart_col] = pd.to_numeric(chart_df[chart_col], errors="coerce")
                    chart_df = chart_df.dropna(subset=[chart_col])
                    chart_df = chart_df.set_index(label_col)

                    tab_bar, tab_line = st.tabs(["Bar", "Line"])
                    with tab_bar:
                        st.bar_chart(chart_df)
                    with tab_line:
                        st.line_chart(chart_df)

        except Exception as exc:
            st.code(json.dumps(parsed["table_rows"], indent=2), language="json")
            st.caption(f"Table render error: {exc}")

    after = parsed["narrative_after"]
    if after and not _is_residual_json(after):
        st.markdown(after)

    if parsed["follow_ups"]:
        st.markdown("**You might also want to ask:**")
        cols = st.columns(len(parsed["follow_ups"]))
        for i, question in enumerate(parsed["follow_ups"]):
            with cols[i]:
                unique_key = f"followup_{abs(hash(question))}_{i}_{abs(hash(content))}"
                if st.button(question, key=unique_key, use_container_width=True):
                    st.session_state.pending_prompt = question
                    st.rerun()