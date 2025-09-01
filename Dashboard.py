"""
Workout Tracker – Streamlit App (Strength + Cardio + Analytics)

Features:
- Mandatory authentication via streamlit-authenticator
- Tabs: Strength, Cardio, Analytics
- Cardio logger (running/cycling/swimming/jump rope or custom): duration, distance, RPE, pain
- Analytics tab: weekly volume per muscle group (primary=1×, secondary=0.5×). Last week, weekly mean, and weekly trend. Cardio volume analytics too
- Automatic GitHub CSV storage (no local files)
- Exercise database from data/exercises.xlsx

Setup:
1. Install dependencies: pip install -r requirements.txt
2. Configure auth.yaml with your credentials
3. Set GitHub environment variables (GITHUB_TOKEN, GITHUB_REPO, etc.)
4. Ensure data/exercises.xlsx exists with proper format

Run:
streamlit run Dashboard.py

Excel schema (data/exercises.xlsx):
Columns (case-insensitive): exercise, primary_muscle, secondary_muscle
"""

from __future__ import annotations
import base64
import io
import os
from datetime import datetime, date
from typing import List, Dict

import altair as alt
import pandas as pd
import requests
import streamlit as st
from dateutil import tz

# ----------------- Config -----------------
DATA_DIR = "data"
EXERCISES_XLSX = os.path.join(DATA_DIR, "exercises.xlsx")
WORKOUTS_CSV = os.path.join(DATA_DIR, "workouts.csv")
CARDIO_CSV = os.path.join(DATA_DIR, "cardio.csv")

DEFAULT_CARDIO = ["Running", "Cycling", "Swimming", "Jump Rope"]

# Page config
st.set_page_config(page_title="Workout Tracker", page_icon="💪", layout="wide")

# ----------------- Mandatory Auth -----------------
def authenticate():
    try:
        import yaml
        import streamlit_authenticator as stauth
    except ImportError as e:
        st.error(f"Auth dependencies not installed: {e}")
        st.stop()
    
    cfg_path = "auth.yaml"
    if not os.path.exists(cfg_path):
        st.error("auth.yaml not found. Please create the authentication config file.")
        st.stop()
    
    try:
        with open(cfg_path, "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        st.error(f"Failed to load auth.yaml: {e}")
        st.stop()
    
    authenticator = stauth.Authenticate(
        config['credentials'],
        config['cookie']['name'],
        config['cookie']['key'],
        config['cookie']['expiry_days']
    )
    
    authenticator.login(location='main')
    
    if st.session_state.get("authentication_status"):
        with st.sidebar:
            st.write(f'Welcome *{st.session_state["name"]}*')
            authenticator.logout('Logout', 'sidebar')
        return True
    elif st.session_state.get("authentication_status") is False:
        st.error('Username/password is incorrect')
        st.stop()
    elif st.session_state.get("authentication_status") is None:
        st.warning('Please enter your username and password')
        st.stop()

# Require authentication
authenticate()

# ----------------- Utilities -----------------

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def normalize_exercise_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename_map = {
        "exercise": "exercise",
        "primary": "primary_muscle",
        "primary muscle": "primary_muscle",
        "primary_muscle": "primary_muscle",
        "secondary": "secondary_muscle",
        "secondary muscle": "secondary_muscle",
        "secondary_muscle": "secondary_muscle",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})
    required = {"exercise", "primary_muscle", "secondary_muscle"}
    missing = required - set(df.columns)
    for m in missing:
        df[m] = ""
    for col in ["exercise", "primary_muscle", "secondary_muscle"]:
        df[col] = df[col].astype(str).str.strip()
    df = df[df["exercise"] != ""].drop_duplicates(subset=["exercise"]).sort_values("exercise")
    return df[["exercise", "primary_muscle", "secondary_muscle"]]


@st.cache_data(show_spinner=False)
def load_exercises(path: str = EXERCISES_XLSX) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            df = pd.read_excel(path)
            return normalize_exercise_df(df)
        except Exception as e:
            st.error(f"Couldn't read {path}: {e}")
            return pd.DataFrame(columns=["exercise", "primary_muscle", "secondary_muscle"])
    else:
        return pd.DataFrame(columns=["exercise", "primary_muscle", "secondary_muscle"])


def today_local_date() -> date:
    tz_local = tz.tzlocal()
    return datetime.now(tz_local).date()


def next_label(prefix: str, counter_key: str) -> str:
    if counter_key not in st.session_state:
        st.session_state[counter_key] = 1
    label = f"{prefix}{st.session_state[counter_key]}"
    st.session_state[counter_key] += 1
    return label


def compute_next_set_no(rows: List[Dict], exercise: str) -> int:
    return 1 + sum(1 for r in rows if r.get("exercise") == exercise)


def df_from_rows(rows: List[Dict], kind: str) -> pd.DataFrame:
    if kind == "strength":
        cols = [
            "workout_date","exercise","set_no","weight","weight_unit","reps","rpe","pain","set_type",
            "superset_group","superset_part","dropset_group","drop_no","timestamp","notes"
        ]
    else:
        cols = [
            "workout_date","activity","duration_min","distance_km","rpe","pain","timestamp","notes"
        ]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)[cols]


def merge_with_exercise_meta(df_sets: pd.DataFrame, df_ex: pd.DataFrame) -> pd.DataFrame:
    return df_sets.merge(df_ex, how="left", on="exercise")


# ---------- GitHub CSV helpers (optional) ----------

def github_env():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO")  # e.g. "username/reponame"
    branch = os.environ.get("GITHUB_BRANCH", "main")
    path_strength = os.environ.get("GITHUB_FILEPATH_STRENGTH", "data/workouts.csv")
    path_cardio = os.environ.get("GITHUB_FILEPATH_CARDIO", "data/cardio.csv")
    return token, repo, branch, path_strength, path_cardio


def github_get_file(token: str, repo: str, path: str, ref: str):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers={"Authorization": f"token {token}"}, params={"ref": ref})
    if r.status_code == 200:
        return r.json()  # has 'content' and 'sha'
    elif r.status_code == 404:
        return None
    else:
        raise RuntimeError(f"GitHub GET failed: {r.status_code} {r.text}")


def github_put_file(token: str, repo: str, path: str, content_b64: str, message: str, branch: str, sha: str | None = None):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    payload = {"message": message, "content": content_b64, "branch": branch}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers={"Authorization": f"token {token}"}, json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT failed: {r.status_code} {r.text}")
    return r.json()


def save_csv_local_append(df_new: pd.DataFrame, csv_path: str):
    ensure_data_dir()
    if os.path.exists(csv_path):
        df_new.to_csv(csv_path, mode="a", header=False, index=False)
    else:
        df_new.to_csv(csv_path, index=False)


def save_csv_github_append(df_new: pd.DataFrame, which: str):
    token, repo, branch, path_strength, path_cardio = github_env()
    if which == "strength":
        path = path_strength
    else:
        path = path_cardio
    if not (token and repo and path and branch):
        return False, "GitHub env vars not fully set; skipped GitHub save."

    meta = github_get_file(token, repo, path, ref=branch)
    if meta is None:
        csv_buf = io.StringIO(); df_new.to_csv(csv_buf, index=False)
        b64 = base64.b64encode(csv_buf.getvalue().encode("utf-8")).decode("utf-8")
        github_put_file(token, repo, path, b64, message=f"Add first {os.path.basename(path)}", branch=branch)
        return True, f"Created {path} in {repo}@{branch}"

    existing_b64 = meta.get("content", "")
    existing_bytes = base64.b64decode(existing_b64)
    try:
        df_existing = pd.read_csv(io.BytesIO(existing_bytes))
        df_out = pd.concat([df_existing, df_new], ignore_index=True)
        csv_buf = io.StringIO(); df_out.to_csv(csv_buf, index=False)
    except Exception:
        csv_buf = io.StringIO(existing_bytes.decode("utf-8"))
        df_new.to_csv(csv_buf, header=False, index=False)
    b64 = base64.b64encode(csv_buf.getvalue().encode("utf-8")).decode("utf-8")
    github_put_file(token, repo, path, b64, message="Append data", branch=branch, sha=meta.get("sha"))
    return True, f"Appended to {path} in {repo}@{branch}"

# ----------------- Sidebar: data & settings -----------------
st.title("💪 Workout Tracker")

ensure_data_dir()

with st.sidebar:
    st.header("Settings")
    st.markdown("**Exercises file:** `data/exercises.xlsx`")

    # Load exercises and show status
    df_ex = load_exercises()
    if df_ex.empty:
        st.error("❌ No exercises found in data/exercises.xlsx")
        st.stop()
    else:
        st.success(f"✅ Loaded {len(df_ex)} exercises")

    st.markdown("---")
    st.markdown("**Save Settings**")
    st.markdown("✅ GitHub push enabled")
    st.markdown("❌ Local CSV disabled")

# ----------------- Tabs -----------------
strength_tab, cardio_tab, analytics_tab = st.tabs(["🏋️ Strength", "🏃 Cardio", "📈 Analytics"])

# ===== Strength tab =====
with strength_tab:
    st.session_state.setdefault("workout_rows", [])
    st.session_state.setdefault("superset_counter", 1)
    st.session_state.setdefault("dropset_counter", 1)

    c1, c2, c3 = st.columns([1,1,2])
    with c1:
        workout_date = st.date_input("Workout date", value=today_local_date(), key="strength_date")
    with c2:
        session_name = st.text_input("Session name (optional)", key="strength_session")
    with c3:
        notes_global = st.text_input("Session notes (optional)", key="strength_notes")

    st.subheader("Add set(s)")
    with st.form("add_set_form", clear_on_submit=False):
        cc1, cc2 = st.columns([2,1])
        with cc1:
            exercise = st.selectbox("Exercise", options=df_ex["exercise"].tolist() if not df_ex.empty else [])
        with cc2:
            weight_unit = st.radio("Unit", options=["kg","lb"], horizontal=True)
        c4, c5, c6, c7 = st.columns(4)
        with c4:
            weight = st.number_input("Weight", min_value=0.0, step=0.5, format="%.2f")
        with c5:
            reps = st.number_input("Reps", min_value=1, step=1, value=8)
        with c6:
            rpe = st.number_input("RPE", min_value=1.0, max_value=10.0, step=0.5, value=8.5, format="%.1f")
        with c7:
            pain = st.number_input("Pain (0-10)", min_value=0.0, max_value=10.0, step=0.5, value=0.0, format="%.1f")
        c8, c9, c10 = st.columns([1,1,2])
        with c8:
            sets_to_add = st.number_input("Sets to add", min_value=1, max_value=20, value=1)
        with c9:
            relation = st.selectbox("Relationship", ["None","Superset","Dropset"], index=0)
        with c10:
            notes = st.text_input("Notes (optional)")

        c11, c12, _ = st.columns([1,1,2])
        superset_group = ""; superset_part = ""; dropset_group = ""; drop_no = None
        if relation == "Superset":
            with c11:
                superset_group = st.text_input("Superset group", value="", placeholder="auto")
            with c12:
                superset_part = st.selectbox("Part", options=["A","B"], index=0)
        elif relation == "Dropset":
            with c11:
                dropset_group = st.text_input("Dropset group", value="", placeholder="auto")
            with c12:
                drop_no = st.number_input("Drop #", min_value=1, step=1, value=1)

        add_btn_col, undo_btn_col, clear_btn_col = st.columns([1,1,1])
        submitted = add_btn_col.form_submit_button("➕ Add set(s)")
        undo_clicked = undo_btn_col.form_submit_button("↩️ Undo last")
        clear_clicked = clear_btn_col.form_submit_button("🗑️ Clear all")

        if submitted:
            if not df_ex.empty and exercise:
                if weight <= 0:
                    st.error("Weight must be greater than 0")
                elif reps <= 0:
                    st.error("Reps must be greater than 0")
                else:
                    rows = st.session_state["workout_rows"]
                    if relation == "Superset" and not superset_group:
                        superset_group = next_label("S", "superset_counter")
                    if relation == "Dropset" and not dropset_group:
                        dropset_group = next_label("D", "dropset_counter")
                    for i in range(int(sets_to_add)):
                        set_no = compute_next_set_no(rows, exercise)
                        rows.append({
                            "workout_date": workout_date.isoformat(),
                            "exercise": exercise,
                            "set_no": set_no,
                            "weight": float(weight),
                            "weight_unit": weight_unit,
                            "reps": int(reps),
                            "rpe": float(rpe),
                            "pain": float(pain),
                            "set_type": relation.lower() if relation != "None" else "normal",
                            "superset_group": superset_group if relation == "Superset" else "",
                            "superset_part": superset_part if relation == "Superset" else "",
                            "dropset_group": dropset_group if relation == "Dropset" else "",
                            "drop_no": int(drop_no) if relation == "Dropset" and drop_no else None,
                            "timestamp": datetime.now().isoformat(timespec="seconds"),
                            "notes": notes.strip(),
                        })
                    st.success(f"Added {int(sets_to_add)} set(s) of {exercise}.")
            else:
                st.warning("Please select an exercise and ensure exercises file is loaded.")
        if undo_clicked:
            if st.session_state["workout_rows"]:
                st.session_state["workout_rows"].pop(); st.info("Removed last set.")
            else:
                st.warning("No rows to remove.")
        if clear_clicked:
            st.session_state["workout_rows"] = []; st.info("Cleared current workout.")

    st.subheader("Current workout")
    df_current = df_from_rows(st.session_state["workout_rows"], kind="strength")
    # Convert workout_date to proper date format for display
    if not df_current.empty:
        df_current_display = df_current.copy()
        df_current_display["workout_date"] = pd.to_datetime(df_current_display["workout_date"]).dt.date
    else:
        df_current_display = df_current
    
    edited = st.data_editor(
        df_current_display,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "workout_date": st.column_config.DateColumn("Date"),
            "set_no": st.column_config.NumberColumn("Set #", step=1),
            "weight": st.column_config.NumberColumn("Weight", step=0.5),
            "reps": st.column_config.NumberColumn("Reps", step=1),
            "rpe": st.column_config.NumberColumn("RPE", step=0.5, min_value=1.0, max_value=10.0),
            "pain": st.column_config.NumberColumn("Pain", step=0.5, min_value=0.0, max_value=10.0),
            "set_type": st.column_config.SelectboxColumn("Set type", options=["normal","superset","dropset"]),
            "superset_part": st.column_config.SelectboxColumn("Superset part", options=["","A","B"]),
        },
        hide_index=True,
    )
    # Convert back to string format for consistency
    if not edited.empty:
        edited["workout_date"] = edited["workout_date"].astype(str)
    st.session_state["workout_rows"] = edited.to_dict(orient="records")
    st.caption("Tip: Edit any cell before saving.")

    if st.button("💾 Save workout", type="primary", key="save_strength"):
        if not st.session_state["workout_rows"]:
            st.warning("No rows to save.")
        else:
            df_sets = df_from_rows(st.session_state["workout_rows"], kind="strength").copy()
            df_sets["session_name"] = session_name; df_sets["session_notes"] = notes_global
            df_out = merge_with_exercise_meta(df_sets, load_exercises())
            cols_first = [
                "workout_date","session_name","exercise","set_no","weight","weight_unit","reps","rpe","pain",
                "set_type","superset_group","superset_part","dropset_group","drop_no","timestamp","notes",
                "session_notes","primary_muscle","secondary_muscle"
            ]
            df_out = df_out[[c for c in cols_first if c in df_out.columns]]
            
            # Save to GitHub only
            try:
                ok, msg = save_csv_github_append(df_out, which="strength")
                if ok:
                    st.success(f"✅ {msg}")
                    st.download_button("⬇️ Download this workout as CSV", df_out.to_csv(index=False).encode("utf-8"),
                                       file_name=f"workout_{workout_date.isoformat()}.csv", mime="text/csv")
                    st.session_state["workout_rows"] = []
                    st.toast("Workout saved to GitHub and cleared.")
                else:
                    st.warning(f"⚠️ {msg}")
            except Exception as e:
                st.error(f"❌ GitHub save failed: {e}")
                st.info("Please check your GitHub environment variables (GITHUB_TOKEN, GITHUB_REPO, etc.)")

# ===== Cardio tab =====
with cardio_tab:
    st.session_state.setdefault("cardio_rows", [])

    c1, c2 = st.columns([1,3])
    with c1:
        c_date = st.date_input("Date", value=today_local_date(), key="cardio_date")
    with c2:
        st.text_input("Session notes (optional)", key="cardio_notes")

    st.subheader("Add cardio entry")
    with st.form("add_cardio_form", clear_on_submit=False):
        c3, c4 = st.columns([2,1])
        with c3:
            activity = st.selectbox("Activity", DEFAULT_CARDIO + ["Other"], index=0)
        with c4:
            custom_activity = st.text_input("If Other, specify", value="")
        c5, c6, c7, c8 = st.columns(4)
        with c5:
            duration = st.number_input("Duration (min)", min_value=0.0, step=1.0, value=0.0, format="%.1f")
        with c6:
            distance = st.number_input("Distance (km)", min_value=0.0, step=0.1, value=0.0, format="%.2f")
        with c7:
            rpe_c = st.number_input("RPE", min_value=1.0, max_value=10.0, step=0.5, value=6.0, format="%.1f")
        with c8:
            pain_c = st.number_input("Pain (0-10)", min_value=0.0, max_value=10.0, step=0.5, value=0.0, format="%.1f")
        notes_c = st.text_input("Notes (optional)")
        add_cardio = st.form_submit_button("➕ Add cardio entry")

        if add_cardio:
            act = custom_activity.strip() if activity == "Other" and custom_activity.strip() else activity
            if activity == "Other" and not custom_activity.strip():
                st.error("Please specify the custom activity")
            elif duration <= 0 and distance <= 0:
                st.error("Please enter either duration or distance (or both)")
            else:
                st.session_state["cardio_rows"].append({
                    "workout_date": c_date.isoformat(),
                    "activity": act,
                    "duration_min": float(duration),
                    "distance_km": float(distance),
                    "rpe": float(rpe_c),
                    "pain": float(pain_c),
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "notes": notes_c.strip(),
                })
                st.success(f"Added {act} entry.")

    st.subheader("Current cardio entries")
    df_cardio_cur = df_from_rows(st.session_state["cardio_rows"], kind="cardio")
    edited_c = st.data_editor(df_cardio_cur, use_container_width=True, num_rows="dynamic", hide_index=True)
    st.session_state["cardio_rows"] = edited_c.to_dict(orient="records")

    save_c = st.button("💾 Save cardio", type="primary", key="save_cardio")
    if save_c:
        if not st.session_state["cardio_rows"]:
            st.warning("No cardio rows to save.")
        else:
            df_out = df_from_rows(st.session_state["cardio_rows"], kind="cardio").copy()
            df_out["session_notes"] = st.session_state.get("cardio_notes", "")
            
            # Save to GitHub only
            try:
                ok, msg = save_csv_github_append(df_out, which="cardio")
                if ok:
                    st.success(f"✅ {msg}")
                    st.download_button("⬇️ Download these cardio entries", df_out.to_csv(index=False).encode("utf-8"),
                                       file_name=f"cardio_{c_date.isoformat()}.csv", mime="text/csv")
                    st.session_state["cardio_rows"] = []
                    st.toast("Cardio saved to GitHub and cleared.")
                else:
                    st.warning(f"⚠️ {msg}")
            except Exception as e:
                st.error(f"❌ GitHub save failed: {e}")
                st.info("Please check your GitHub environment variables (GITHUB_TOKEN, GITHUB_REPO, etc.)")

    with st.expander("Optional: Import cardio from Strava export (.GPX/.TCX/.FIT) — advanced"):
        st.caption("You can connect COROS → Strava and export files, then parse locally with libraries like fitparse (FIT) or tcxreader (TCX). This keeps the app simple and private.")
        st.markdown("Libraries to consider: `fitparse`, `gpxpy`, `tcxreader`.")

    st.info("Tip: COROS doesn't offer a self-serve personal API yet. Easiest pipeline is COROS → Strava → export/API (see Notes below).")

# ===== Analytics tab =====
with analytics_tab:
    st.subheader("Strength volume by muscle group")
    metric = st.selectbox("Volume metric", ["Sets","Reps","Tonnage (Weight×Reps)"])
    week_ending = st.selectbox("Week ends on", ["Mon","Sun"], index=0)

    def load_strength() -> pd.DataFrame:
        if os.path.exists(WORKOUTS_CSV):
            df = pd.read_csv(WORKOUTS_CSV)
            df["workout_date"] = pd.to_datetime(df["workout_date"], errors="coerce")
            return df
        else:
            # Try to load from GitHub if local file doesn't exist
            try:
                token, repo, branch, path_strength, path_cardio = github_env()
                if token and repo and path_strength:
                    meta = github_get_file(token, repo, path_strength, ref=branch)
                    if meta:
                        existing_b64 = meta.get("content", "")
                        existing_bytes = base64.b64decode(existing_b64)
                        df = pd.read_csv(io.BytesIO(existing_bytes))
                        df["workout_date"] = pd.to_datetime(df["workout_date"], errors="coerce")
                        return df
            except Exception as e:
                st.info(f"Could not load from GitHub: {e}")
            return pd.DataFrame()

    def load_cardio() -> pd.DataFrame:
        if os.path.exists(CARDIO_CSV):
            df = pd.read_csv(CARDIO_CSV)
            df["workout_date"] = pd.to_datetime(df["workout_date"], errors="coerce")
            return df
        else:
            # Try to load from GitHub if local file doesn't exist
            try:
                token, repo, branch, path_strength, path_cardio = github_env()
                if token and repo and path_cardio:
                    meta = github_get_file(token, repo, path_cardio, ref=branch)
                    if meta:
                        existing_b64 = meta.get("content", "")
                        existing_bytes = base64.b64decode(existing_b64)
                        df = pd.read_csv(io.BytesIO(existing_bytes))
                        df["workout_date"] = pd.to_datetime(df["workout_date"], errors="coerce")
                        return df
            except Exception as e:
                st.info(f"Could not load cardio from GitHub: {e}")
            return pd.DataFrame()

    def week_period(s: pd.Series) -> pd.Series:
        freq = "W-MON" if week_ending == "Mon" else "W-SUN"
        return s.dt.to_period(freq)

    df_s = load_strength()
    if df_s.empty:
        st.warning("No strength data yet. Save a workout to populate analytics.")
    else:
        # compute per-row volume
        if metric == "Sets":
            df_s["row_volume"] = 1.0
        elif metric == "Reps":
            df_s["row_volume"] = df_s.get("reps", 0)
        else:
            df_s["row_volume"] = df_s.get("reps", 0) * df_s.get("weight", 0)
        # allocate to muscles
        rows = []
        for _, r in df_s.iterrows():
            d = r.get("workout_date")
            vol = float(r.get("row_volume", 0))
            prim = str(r.get("primary_muscle", "")).strip()
            sec = str(r.get("secondary_muscle", "")).strip()
            if prim:
                rows.append({"date": d, "muscle": prim, "volume": vol*1.0})
            if sec:
                rows.append({"date": d, "muscle": sec, "volume": vol*0.5})
        df_m = pd.DataFrame(rows)
        if df_m.empty:
            st.info("No muscle metadata found in workouts. Make sure exercises.xlsx includes primary/secondary.")
        else:
            df_m["week"] = week_period(pd.to_datetime(df_m["date"]))
            weekly = df_m.groupby(["week","muscle"], as_index=False)["volume"].sum()
            # Last week and mean
            today = pd.Timestamp.today().normalize()
            cur_week = week_period(pd.Series([today]))[0]
            last_week = (cur_week - 1)
            last_week_df = weekly[weekly["week"] == last_week]
            mean_df = weekly.groupby("muscle", as_index=False)["volume"].mean().rename(columns={"volume":"weekly_mean"})
            lw_pivot = last_week_df.pivot(index="muscle", columns="week", values="volume").fillna(0)
            lw_sorted = lw_pivot.sort_values(by=lw_pivot.columns[0], ascending=False)
            st.markdown("**Last week (" + str(last_week) + ")**")
            st.dataframe(lw_sorted, use_container_width=True)
            st.markdown("**Weekly mean (all weeks)**")
            st.dataframe(mean_df.sort_values("weekly_mean", ascending=False), use_container_width=True)

            # Trend chart (top muscles)
            top_muscles = mean_df.sort_values("weekly_mean", ascending=False)["muscle"].head(8).tolist()
            trend = weekly[weekly["muscle"].isin(top_muscles)].copy()
            # Convert period to timestamp (end of period)
            trend["week_end"] = trend["week"].dt.end_time
            chart = alt.Chart(trend).mark_line(point=True).encode(
                x=alt.X("week_end:T", title="Week"),
                y=alt.Y("volume:Q", title=f"{metric}"),
                color="muscle:N",
                tooltip=["muscle","week_end:T","volume:Q"]
            ).properties(height=300, use_container_width=True)
            st.altair_chart(chart, use_container_width=True)

    st.divider()
    st.subheader("Cardio volume")
    df_c = load_cardio()
    if df_c.empty:
        st.info("No cardio saved yet.")
    else:
        df_c["week"] = week_period(pd.to_datetime(df_c["workout_date"]))
        weekly_c = df_c.groupby(["week","activity"], as_index=False).agg({
            "duration_min":"sum","distance_km":"sum","pain":"mean"})
        today = pd.Timestamp.today().normalize()
        cur_week = week_period(pd.Series([today]))[0]
        last_week = (cur_week - 1)
        st.markdown("**Last week (" + str(last_week) + ")**")
        last_c = weekly_c[weekly_c["week"] == last_week].copy()
        st.dataframe(last_c.sort_values("duration_min", ascending=False), use_container_width=True)

        mean_c = weekly_c.groupby("activity", as_index=False)[["duration_min","distance_km","pain"]].mean()
        st.markdown("**Weekly mean (all weeks)**")
        st.dataframe(mean_c.sort_values("duration_min", ascending=False), use_container_width=True)

        # Trend charts
        weekly_c["week_end"] = weekly_c["week"].dt.end_time
        ch1 = alt.Chart(weekly_c).mark_line(point=True).encode(
            x=alt.X("week_end:T", title="Week"),
            y=alt.Y("duration_min:Q", title="Minutes"),
            color="activity:N",
            tooltip=["activity","week_end:T","duration_min:Q"]
        ).properties(height=300)
        ch2 = alt.Chart(weekly_c).mark_line(point=True).encode(
            x=alt.X("week_end:T", title="Week"),
            y=alt.Y("distance_km:Q", title="Kilometers"),
            color="activity:N",
            tooltip=["activity","week_end:T","distance_km:Q"]
        ).properties(height=300)
        st.altair_chart(ch1, use_container_width=True)
        st.altair_chart(ch2, use_container_width=True)

# ----------------- Footer -----------------
st.markdown(
    """
    ---
    **Configuration & Notes**
    
    **🔐 Authentication**: Required - configure `auth.yaml` with your credentials.
    
    **📊 Data Storage**: All workouts are automatically saved to GitHub. Configure the following environment variables:
    - `GITHUB_TOKEN`: Your GitHub personal access token
    - `GITHUB_REPO`: Repository name (format: `username/reponame`)
    - `GITHUB_BRANCH`: Branch name (default: `main`)
    - `GITHUB_FILEPATH_STRENGTH`: Path for strength data (default: `data/workouts.csv`)
    - `GITHUB_FILEPATH_CARDIO`: Path for cardio data (default: `data/cardio.csv`)
    
    **🏋️ Strength Tracking**: Each row represents one set. For different weights/reps of the same exercise, add multiple rows. Use supersets/dropsets via shared groups.
    
    **📈 Analytics**: Primary muscles count 1× volume, secondary muscles count 0.5×. Choose between Sets/Reps/Tonnage metrics.
    
    **🏃 Cardio Tracking**: Volume tracked in minutes and kilometers. Add custom activities via "Other" option.
    
    **📱 COROS Integration**: Simplest route is COROS → Strava (auto-sync) → export files → optional parsing. Direct COROS API requires partner approval.
    
    **🛠️ Setup**: Ensure `data/exercises.xlsx` exists with columns: exercise, primary_muscle, secondary_muscle
    """
)
