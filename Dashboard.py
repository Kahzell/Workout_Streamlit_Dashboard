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

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()  # This loads the .env file
except ImportError:
    pass  # dotenv not installed, skip loading

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
    
    # Try to load from secrets first, then fall back to local file
    config = None
    try:
        if "auth" in st.secrets:
            config = st.secrets["auth"]
        else:
            # Fallback to local auth.yaml file
            cfg_path = "auth.yaml"
            if not os.path.exists(cfg_path):
                st.error("auth.yaml not found and no auth secrets configured.")
                st.stop()
            
            with open(cfg_path, "r") as f:
                config = yaml.safe_load(f)
    except Exception as e:
        st.error(f"Failed to load authentication config: {e}")
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
        "variation": "variation",
        "variant": "variation",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})
    required = {"exercise", "primary_muscle", "secondary_muscle", "variation"}
    missing = required - set(df.columns)
    for m in missing:
        df[m] = ""
    for col in ["exercise", "primary_muscle", "secondary_muscle", "variation"]:
        df[col] = df[col].astype(str).str.strip()
    df = df[df["exercise"] != ""].drop_duplicates(subset=["exercise", "variation"]).sort_values(["exercise", "variation"])
    return df[["exercise", "primary_muscle", "secondary_muscle", "variation"]]


@st.cache_data(show_spinner=False)
def load_exercises(path: str = EXERCISES_XLSX) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            df = pd.read_excel(path)
            return normalize_exercise_df(df)
        except Exception as e:
            st.error(f"Couldn't read {path}: {e}")
            return pd.DataFrame(columns=["exercise", "primary_muscle", "secondary_muscle", "variation"])
    else:
        return pd.DataFrame(columns=["exercise", "primary_muscle", "secondary_muscle", "variation"])


def today_local_date() -> date:
    tz_local = tz.tzlocal()
    return datetime.now(tz_local).date()


def next_label(prefix: str, counter_key: str) -> str:
    if counter_key not in st.session_state:
        st.session_state[counter_key] = 1
    label = f"{prefix}{st.session_state[counter_key]}"
    st.session_state[counter_key] += 1
    return label


def get_exercise_variations(df_ex: pd.DataFrame, exercise: str) -> List[str]:
    """Get available variations for a specific exercise"""
    if df_ex.empty:
        return []
    variations = df_ex[df_ex["exercise"] == exercise]["variation"].tolist()
    # Remove empty variations and duplicates
    variations = [v for v in variations if v.strip()]
    return list(set(variations)) if variations else []


def get_exercise_with_variation(exercise: str, variation: str) -> str:
    """Combine exercise name with variation for storage"""
    if not variation or variation == "Standard":
        return exercise
    return f"{exercise} ({variation})"


def create_exercise_selection(df_ex: pd.DataFrame, key: str = None) -> str:
    """Create exercise selection with variation options"""
    if df_ex.empty:
        return ""
    
    # Create exercise options with variations
    options = []
    for _, row in df_ex.iterrows():
        base_exercise = row["exercise"]
        variations = get_exercise_variations(df_ex, base_exercise)
        
        if variations:
            for var in variations:
                if var != base_exercise:  # Don't duplicate the base exercise
                    options.append(f"{base_exercise} ({var})")
        options.append(base_exercise)
    
    # Remove duplicates and sort
    options = sorted(list(set(options)))
    
    kwargs = {"options": options}
    if key:
        kwargs["key"] = key
    
    return st.selectbox("Exercise", **kwargs)


def create_notes_input(key_prefix: str, placeholder: str = "Notes (optional)") -> str:
    """Create notes input with predefined options"""
    col1, col2 = st.columns([2, 1])
    
    with col2:
        quick_note = st.selectbox(
            "Quick notes", 
            ["None", "Rehab", "Deload", "Other gym"], 
            key=f"{key_prefix}_quick"
        )
    
    with col1:
        custom_note = st.text_input(placeholder, key=f"{key_prefix}_custom")
    
    # Combine notes
    notes = []
    if quick_note != "None":
        notes.append(quick_note)
    if custom_note.strip():
        notes.append(custom_note.strip())
    
    return " | ".join(notes)


def get_next_superset_name() -> str:
    """Generate next superset name with better naming"""
    existing_supersets = set()
    for row in st.session_state.get("workout_rows", []):
        if row.get("set_type") == "superset" and row.get("superset_group"):
            existing_supersets.add(row["superset_group"])
    
    counter = 1
    while f"Superset-{counter}" in existing_supersets:
        counter += 1
    return f"Superset-{counter}"


def create_exercise_variant_inputs(df_ex: pd.DataFrame, key_prefix: str = ""):
    """Create separate exercise and variant dropdowns that update dynamically"""
    col1, col2 = st.columns([2, 1])
    
    with col1:
        exercise = st.selectbox(
            "Exercise", 
            options=df_ex["exercise"].unique().tolist() if not df_ex.empty else [],
            key=f"{key_prefix}_exercise" if key_prefix else None
        )
    
    with col2:
        if exercise and not df_ex.empty:
            # Get variations for the selected exercise
            variations = get_exercise_variations(df_ex, exercise)
            if variations:
                # Use exercise name in key to force refresh when exercise changes
                variant_key = f"{key_prefix}_variant_{exercise.replace(' ', '_')}" if key_prefix else f"variant_{exercise.replace(' ', '_')}"
                variant = st.selectbox(
                    "Variant (optional)",
                    options=[""] + variations,
                    key=variant_key
                )
            else:
                # Show disabled selectbox when no variants
                variant = ""
                disabled_key = f"{key_prefix}_variant_disabled_{exercise.replace(' ', '_')}" if key_prefix else f"variant_disabled_{exercise.replace(' ', '_')}"
                st.selectbox(
                    "Variant (optional)", 
                    options=["No variants available"], 
                    disabled=True, 
                    key=disabled_key
                )
        else:
            variant = ""
            empty_key = f"{key_prefix}_variant_empty" if key_prefix else "variant_empty"
            st.selectbox("Variant (optional)", options=[""], disabled=True, key=empty_key)
    
    return exercise, variant


def load_workout_history() -> pd.DataFrame:
    """Load all workout history from GitHub"""
    try:
        token, repo, branch, path_strength, path_cardio = github_env()
        
        all_data = []
        
        # Load strength data
        if token and repo and path_strength:
            strength_meta = github_get_file(token, repo, path_strength, ref=branch)
            if strength_meta:
                strength_b64 = strength_meta.get("content", "")
                strength_bytes = base64.b64decode(strength_b64)
                strength_data = pd.read_csv(io.BytesIO(strength_bytes))
                if not strength_data.empty:
                    strength_data['workout_type'] = 'strength'
                    all_data.append(strength_data)
        
        # Load cardio data  
        if token and repo and path_cardio:
            cardio_meta = github_get_file(token, repo, path_cardio, ref=branch)
            if cardio_meta:
                cardio_b64 = cardio_meta.get("content", "")
                cardio_bytes = base64.b64decode(cardio_b64)
                cardio_data = pd.read_csv(io.BytesIO(cardio_bytes))
                if not cardio_data.empty:
                    cardio_data['workout_type'] = 'cardio'
                    all_data.append(cardio_data)
        
        # Combine data
        if all_data:
            combined_df = pd.concat(all_data, ignore_index=True)
            # Ensure variant column exists
            if 'variant' not in combined_df.columns:
                combined_df['variant'] = ""
            return combined_df
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Error loading workout history: {e}")
        return pd.DataFrame()


def search_exercise_history(df_history: pd.DataFrame, exercise: str, variant: str = "", muscle_group: str = "") -> pd.DataFrame:
    """Search workout history for specific exercise/variant or muscle group"""
    if df_history.empty:
        return pd.DataFrame()
    
    filtered_df = df_history.copy()
    
    if muscle_group:
        # Filter by muscle group - need to merge with exercise data
        df_ex = load_exercises()
        if not df_ex.empty:
            exercises_in_group = df_ex[df_ex["primary_muscle"] == muscle_group]["exercise"].unique()
            filtered_df = filtered_df[filtered_df["exercise"].isin(exercises_in_group)]
    elif exercise:
        # Filter by specific exercise
        filtered_df = filtered_df[filtered_df["exercise"] == exercise]
        if variant:
            filtered_df = filtered_df[filtered_df.get("variant", "") == variant]
    
    # Sort by date descending to get most recent first
    if 'date' in filtered_df.columns:
        filtered_df = filtered_df.sort_values('date', ascending=False)
    
    return filtered_df


def display_exercise_history_search(df_history: pd.DataFrame, df_ex: pd.DataFrame):
    """Display search interface for exercise history"""
    st.markdown("### 📊 Exercise History Search")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        search_type = st.radio(
            "Search by:",
            ["Specific Exercise", "Primary Muscle Group"],
            horizontal=True
        )
    
    if search_type == "Specific Exercise":
        with col2:
            if not df_ex.empty:
                search_exercise = st.selectbox(
                    "Exercise to search",
                    options=[""] + df_ex["exercise"].unique().tolist(),
                    key="history_search_exercise"
                )
            else:
                search_exercise = ""
                st.selectbox("Exercise to search", options=[""], disabled=True)
        
        with col3:
            if search_exercise and not df_ex.empty:
                variations = get_exercise_variations(df_ex, search_exercise)
                if variations:
                    search_variant = st.selectbox(
                        "Variant (optional)",
                        options=[""] + variations,
                        key="history_search_variant"
                    )
                else:
                    search_variant = ""
                    st.selectbox("Variant (optional)", options=[""], disabled=True)
            else:
                search_variant = ""
                st.selectbox("Variant (optional)", options=[""], disabled=True)
        
        if search_exercise:
            history_results = search_exercise_history(df_history, search_exercise, search_variant)
            if not history_results.empty:
                st.markdown(f"**Recent history for {search_exercise}**" + (f" ({search_variant})" if search_variant else ""))
                
                # Display last 10 entries
                display_cols = ['date', 'exercise', 'variant', 'weight', 'reps', 'sets', 'rpe', 'notes']
                available_cols = [col for col in display_cols if col in history_results.columns]
                
                st.dataframe(
                    history_results[available_cols].head(10),
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("No history found for this exercise/variant")
    
    else:  # Primary Muscle Group
        with col2:
            if not df_ex.empty:
                muscle_groups = df_ex["primary_muscle"].unique().tolist()
                search_muscle = st.selectbox(
                    "Primary muscle group",
                    options=[""] + muscle_groups,
                    key="history_search_muscle"
                )
            else:
                search_muscle = ""
                st.selectbox("Primary muscle group", options=[""], disabled=True)
        
        if search_muscle:
            history_results = search_exercise_history(df_history, "", "", search_muscle)
            if not history_results.empty:
                st.markdown(f"**Last entries for {search_muscle} exercises:**")
                
                # Get the most recent entry for each exercise
                if 'date' in history_results.columns:
                    latest_entries = history_results.groupby(['exercise', 'variant']).first().reset_index()
                else:
                    latest_entries = history_results.groupby(['exercise', 'variant']).last().reset_index()
                
                display_cols = ['exercise', 'variant', 'date', 'weight', 'reps', 'sets', 'rpe']
                available_cols = [col for col in display_cols if col in latest_entries.columns]
                
                st.dataframe(
                    latest_entries[available_cols],
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("No history found for this muscle group")
    
    st.markdown("---")


def get_next_dropset_name() -> str:
    """Generate next dropset name with better naming"""
    existing_dropsets = set()
    for row in st.session_state.get("workout_rows", []):
        if row.get("set_type") == "dropset" and row.get("dropset_group"):
            existing_dropsets.add(row["dropset_group"])
    
    counter = 1
    while f"Dropset-{counter}" in existing_dropsets:
        counter += 1
    return f"Dropset-{counter}"


def compute_next_set_no(rows: List[Dict], exercise: str) -> int:
    return 1 + sum(1 for r in rows if r.get("exercise") == exercise)


def df_from_rows(rows: List[Dict], kind: str) -> pd.DataFrame:
    if kind == "strength":
        cols = [
            "workout_date","exercise","variant","set_no","weight","weight_unit","reps","rpe","pain","set_type",
            "superset_group","superset_part","dropset_group","drop_no","timestamp","notes"
        ]
    else:
        cols = [
            "workout_date","activity","duration_min","distance_km","rpe","pain","timestamp","notes"
        ]
    if not rows:
        return pd.DataFrame(columns=cols)
    
    # Create DataFrame and ensure all columns exist
    df = pd.DataFrame(rows)
    for col in cols:
        if col not in df.columns:
            df[col] = "" if col in ["variant", "notes", "superset_group", "superset_part", "dropset_group"] else (0 if col in ["weight", "reps", "rpe", "pain", "set_no", "drop_no"] else "")
    
    return df[cols]


def merge_with_exercise_meta(df_sets: pd.DataFrame, df_ex: pd.DataFrame) -> pd.DataFrame:
    return df_sets.merge(df_ex, how="left", on="exercise")


def calculate_set_volume(row, metric="Sets"):
    """Calculate volume for a single set row, accounting for set type"""
    if metric == "Sets":
        return 1.0
    elif metric == "Reps":
        return float(row.get("reps", 0))
    else:  # Tonnage
        return float(row.get("reps", 0)) * float(row.get("weight", 0))


def get_workout_insights(workout_rows):
    """Generate insights about the current workout"""
    if not workout_rows:
        return {}
    
    total_sets = len(workout_rows)
    exercises = list(set(row["exercise"] for row in workout_rows))
    
    # Count by set type
    normal_count = sum(1 for row in workout_rows if row.get("set_type") == "normal")
    
    # Count unique superset and dropset groups
    superset_groups = set()
    dropset_groups = set()
    
    for row in workout_rows:
        if row.get("set_type") == "superset" and row.get("superset_group"):
            superset_groups.add(row.get("superset_group"))
        elif row.get("set_type") == "dropset" and row.get("dropset_group"):
            dropset_groups.add(row.get("dropset_group"))
    
    superset_count = len(superset_groups)
    dropset_count = len(dropset_groups)
    
    # Calculate estimated workout time (more accurate)
    # Normal sets: 2-3 min each
    # Supersets: Calculate based on exercises in group + rest
    # Dropsets: Calculate based on drops in group + rest
    
    estimated_time = normal_count * 2.5
    
    # For supersets: time depends on exercises and rounds
    for group in superset_groups:
        group_exercises = set()
        group_rounds = 0
        for row in workout_rows:
            if row.get("superset_group") == group:
                group_exercises.add(row["exercise"])
        
        # Count rounds (sets of same exercise in group)
        if group_exercises:
            first_exercise = list(group_exercises)[0]
            group_rounds = sum(1 for row in workout_rows 
                             if row.get("superset_group") == group and row["exercise"] == first_exercise)
        
        # Time: (exercises * 1.5 min) + (rest between rounds * 2 min)
        estimated_time += (len(group_exercises) * 1.5 * group_rounds) + (group_rounds * 2)
    
    # For dropsets: time depends on drops and rounds
    for group in dropset_groups:
        group_drops = 0
        group_rounds = 0
        drops_in_sequence = set()
        
        for row in workout_rows:
            if row.get("dropset_group") == group:
                drops_in_sequence.add(row.get("drop_no", 1))
        
        group_drops = len(drops_in_sequence)
        if group_drops > 0:
            total_group_sets = sum(1 for row in workout_rows if row.get("dropset_group") == group)
            group_rounds = total_group_sets // group_drops
        
        # Time: (drops * 1 min) + (rest between rounds * 3 min)
        estimated_time += (group_drops * 1 * group_rounds) + (group_rounds * 3)
    
    return {
        "total_sets": total_sets,
        "unique_exercises": len(exercises),
        "normal_sets": normal_count,
        "superset_groups": superset_count,
        "dropset_groups": dropset_count,
        "estimated_time_min": int(estimated_time)
    }


# ---------- GitHub CSV helpers (optional) ----------

def github_env():
    try:
        # Use Streamlit secrets for cloud deployment, fallback to env vars for local dev
        token = st.secrets.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
        repo = st.secrets.get("GITHUB_REPO") or os.environ.get("GITHUB_REPO")
        branch = st.secrets.get("GITHUB_BRANCH", "main") or os.environ.get("GITHUB_BRANCH", "main")
        path_strength = st.secrets.get("GITHUB_FILEPATH_STRENGTH", "data/workouts.csv") or os.environ.get("GITHUB_FILEPATH_STRENGTH", "data/workouts.csv")
        path_cardio = st.secrets.get("GITHUB_FILEPATH_CARDIO", "data/cardio.csv") or os.environ.get("GITHUB_FILEPATH_CARDIO", "data/cardio.csv")
        return token, repo, branch, path_strength, path_cardio
    except Exception:
        # Fallback to environment variables only
        token = os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPO")
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
        
        # Debug: Show exercises with variations
        exercises_with_variants = df_ex[df_ex["variation"].str.strip() != ""]
        if not exercises_with_variants.empty:
            st.info(f"🔄 {len(exercises_with_variants)} exercises have variants")
            with st.expander("View exercises with variants"):
                st.dataframe(exercises_with_variants[["exercise", "variation"]])
        else:
            st.warning("⚠️ No exercises found with variants")

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

    # Add exercise history search
    df_history = load_workout_history()
    if not df_history.empty:
        display_exercise_history_search(df_history, df_ex)

    st.subheader("Add set(s)")
    
    # Set type selection at the top
    set_type = st.radio("Set Type", ["Normal Sets", "Superset", "Dropset"], horizontal=True, key="set_type_selection")
    
    if set_type == "Normal Sets":
        # Exercise selection outside form for dynamic updates
        cc1, cc2 = st.columns([2,1])
        with cc1:
            exercise, variant = create_exercise_variant_inputs(df_ex, "normal")
        with cc2:
            weight_unit = st.radio("Unit", options=["kg","lb"], horizontal=True, key="normal_weight_unit")
        
        with st.form("add_normal_sets_form", clear_on_submit=False):
            c4, c5, c6, c7 = st.columns(4)
            with c4:
                weight = st.number_input("Weight", min_value=0.0, step=0.5, format="%.2f")
            with c5:
                reps = st.number_input("Reps", min_value=1, step=1, value=8)
            with c6:
                rpe = st.number_input("RPE", min_value=1.0, max_value=10.0, step=0.5, value=8.5, format="%.1f")
            with c7:
                pain = st.number_input("Pain (0-10)", min_value=0.0, max_value=10.0, step=0.5, value=0.0, format="%.1f")
            
            c8, c9 = st.columns([1,2])
            with c8:
                sets_to_add = st.number_input("Sets to add", min_value=1, max_value=20, value=1)
            with c9:
                notes = create_notes_input("normal_notes", "Notes (optional)")

            submitted = st.form_submit_button("➕ Add Normal Set(s)")
            
            if submitted:
                if not df_ex.empty and exercise:
                    if weight <= 0:
                        st.error("Weight must be greater than 0")
                    elif reps <= 0:
                        st.error("Reps must be greater than 0")
                    else:
                        rows = st.session_state["workout_rows"]
                        for i in range(int(sets_to_add)):
                            set_no = compute_next_set_no(rows, exercise)
                            rows.append({
                                "workout_date": workout_date.isoformat(),
                                "exercise": exercise,
                                "variant": variant.strip() if variant else "",
                                "set_no": set_no,
                                "weight": float(weight),
                                "weight_unit": weight_unit,
                                "reps": int(reps),
                                "rpe": float(rpe),
                                "pain": float(pain),
                                "set_type": "normal",
                                "superset_group": "",
                                "superset_part": "",
                                "dropset_group": "",
                                "drop_no": None,
                                "timestamp": datetime.now().isoformat(timespec="seconds"),
                                "notes": notes.strip(),
                            })
                        st.success(f"Added {int(sets_to_add)} set(s) of {exercise}.")
                else:
                    st.warning("Please select an exercise and ensure exercises file is loaded.")

    elif set_type == "Superset":
        with st.form("add_superset_form", clear_on_submit=False):
            st.markdown("**Configure Superset** (2-4 exercises performed back-to-back)")
            
            # Superset group name
            superset_group = st.text_input("Superset Name", value="", placeholder=f"Auto: {get_next_superset_name()}")
            
            # Number of exercises in superset
            num_exercises = st.number_input("Number of exercises in superset", min_value=2, max_value=4, value=2)
            
            superset_exercises = []
            
            for i in range(num_exercises):
                st.markdown(f"**Exercise {i+1}:**")
                col1, col2, col3, col4, col5, col6 = st.columns([2, 1, 1, 1, 1, 1])
                
                with col1:
                    ex, ex_variant = create_exercise_variant_inputs(df_ex, key_prefix=f"ss_{i}")
                with col2:
                    w = st.number_input(f"Weight", min_value=0.0, step=0.5, format="%.2f", key=f"ss_weight_{i}")
                with col3:
                    r = st.number_input(f"Reps", min_value=1, step=1, value=8, key=f"ss_reps_{i}")
                with col4:
                    rpe_val = st.number_input(f"RPE", min_value=1.0, max_value=10.0, step=0.5, value=8.5, format="%.1f", key=f"ss_rpe_{i}")
                with col5:
                    pain_val = st.number_input(f"Pain", min_value=0.0, max_value=10.0, step=0.5, value=0.0, format="%.1f", key=f"ss_pain_{i}")
                with col6:
                    unit = st.radio(f"Unit", options=["kg","lb"], horizontal=True, key=f"ss_unit_{i}")
                
                notes_ex = create_notes_input(f"ss_notes_{i}", f"Notes for {ex if ex else 'Exercise'} (optional)")
                
                superset_exercises.append({
                    "exercise": ex,
                    "variant": ex_variant.strip() if ex_variant else "",
                    "weight": w,
                    "reps": r,
                    "rpe": rpe_val,
                    "pain": pain_val,
                    "unit": unit,
                    "notes": notes_ex
                })
            
            rounds = st.number_input("Number of superset rounds", min_value=1, max_value=10, value=1)
            
            submitted_ss = st.form_submit_button("➕ Add Complete Superset")
            
            if submitted_ss:
                if not df_ex.empty and all(ex["exercise"] for ex in superset_exercises):
                    # Validate all exercises have positive weight and reps
                    valid = True
                    for ex in superset_exercises:
                        if ex["weight"] <= 0 or ex["reps"] <= 0:
                            st.error(f"All exercises must have weight > 0 and reps > 0")
                            valid = False
                            break
                    
                    if valid:
                        rows = st.session_state["workout_rows"]
                        if not superset_group:
                            superset_group = get_next_superset_name()
                        
                        parts = ["A", "B", "C", "D"]
                        
                        for round_num in range(rounds):
                            for idx, ex in enumerate(superset_exercises):
                                set_no = compute_next_set_no(rows, ex["exercise"])
                                rows.append({
                                    "workout_date": workout_date.isoformat(),
                                    "exercise": ex["exercise"],
                                    "variant": ex["variant"],
                                    "set_no": set_no,
                                    "weight": float(ex["weight"]),
                                    "weight_unit": ex["unit"],
                                    "reps": int(ex["reps"]),
                                    "rpe": float(ex["rpe"]),
                                    "pain": float(ex["pain"]),
                                    "set_type": "superset",
                                    "superset_group": superset_group,
                                    "superset_part": parts[idx],
                                    "dropset_group": "",
                                    "drop_no": None,
                                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                                    "notes": ex["notes"].strip(),
                                })
                        
                        st.success(f"Added superset '{superset_group}' with {rounds} round(s) and {len(superset_exercises)} exercises.")
                else:
                    st.warning("Please select all exercises and ensure exercises file is loaded.")

    elif set_type == "Dropset":
        # Exercise selection outside form for dynamic updates
        st.markdown("**Configure Dropset** (Same exercise with decreasing weight)")
        
        # Exercise selection
        exercise, variant = create_exercise_variant_inputs(df_ex, "dropset")
        weight_unit = st.radio("Unit", options=["kg","lb"], horizontal=True, key="dropset_weight_unit")
        
        with st.form("add_dropset_form", clear_on_submit=False):
            # Dropset group name
            dropset_group = st.text_input("Dropset Name", value="", placeholder=f"Auto: {get_next_dropset_name()}")
            
            # Number of drops
            num_drops = st.number_input("Number of weight drops", min_value=2, max_value=6, value=3)
            
            st.markdown("**Configure each drop:**")
            
            dropset_drops = []
            
            for i in range(num_drops):
                col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
                
                with col1:
                    w = st.number_input(f"Drop {i+1} Weight", min_value=0.0, step=0.5, format="%.2f", key=f"ds_weight_{i}")
                with col2:
                    r = st.number_input(f"Drop {i+1} Reps", min_value=1, step=1, value=max(8-i, 3), key=f"ds_reps_{i}")
                with col3:
                    rpe_val = st.number_input(f"Drop {i+1} RPE", min_value=1.0, max_value=10.0, step=0.5, value=8.5+i*0.5, format="%.1f", key=f"ds_rpe_{i}")
                with col4:
                    pain_val = st.number_input(f"Drop {i+1} Pain", min_value=0.0, max_value=10.0, step=0.5, value=0.0, format="%.1f", key=f"ds_pain_{i}")
                
                dropset_drops.append({
                    "weight": w,
                    "reps": r,
                    "rpe": rpe_val,
                    "pain": pain_val
                })
            
            # Add rounds option for dropsets
            rounds = st.number_input("Number of dropset rounds", min_value=1, max_value=5, value=1, help="How many times to repeat this dropset sequence")
            
            notes = create_notes_input("dropset_notes", "Notes for dropset (optional)")
            
            submitted_ds = st.form_submit_button("➕ Add Complete Dropset")
            
            if submitted_ds:
                if not df_ex.empty and exercise:
                    # Validate all drops have positive weight and reps
                    valid = True
                    for i, drop in enumerate(dropset_drops):
                        if drop["weight"] <= 0 or drop["reps"] <= 0:
                            st.error(f"All drops must have weight > 0 and reps > 0")
                            valid = False
                            break
                        # Check that weight decreases
                        if i > 0 and drop["weight"] >= dropset_drops[i-1]["weight"]:
                            st.error(f"Weight should decrease with each drop")
                            valid = False
                            break
                    
                    if valid:
                        rows = st.session_state["workout_rows"]
                        if not dropset_group:
                            dropset_group = get_next_dropset_name()
                        
                        for round_num in range(rounds):
                            for idx, drop in enumerate(dropset_drops):
                                set_no = compute_next_set_no(rows, exercise)
                                rows.append({
                                    "workout_date": workout_date.isoformat(),
                                    "exercise": exercise,
                                    "variant": variant.strip() if variant else "",
                                    "set_no": set_no,
                                    "weight": float(drop["weight"]),
                                    "weight_unit": weight_unit,
                                    "reps": int(drop["reps"]),
                                    "rpe": float(drop["rpe"]),
                                    "pain": float(drop["pain"]),
                                    "set_type": "dropset",
                                    "superset_group": "",
                                    "superset_part": "",
                                    "dropset_group": dropset_group,
                                    "drop_no": idx + 1,
                                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                                    "notes": notes.strip(),
                                })
                        
                        st.success(f"Added dropset '{dropset_group}' with {rounds} round(s) and {len(dropset_drops)} drops.")
                else:
                    st.warning("Please select an exercise and ensure exercises file is loaded.")

    # Common action buttons
    col1, col2, col3 = st.columns([1,1,1])
    with col1:
        if st.button("↩️ Undo Last Entry"):
            if st.session_state["workout_rows"]:
                removed = st.session_state["workout_rows"].pop()
                st.info(f"Removed last entry: {removed.get('exercise', 'Unknown')}")
            else:
                st.warning("No entries to remove.")
    with col2:
        if st.button("🗑️ Clear All Entries"):
            st.session_state["workout_rows"] = []
            st.info("Cleared all workout entries.")
    with col3:
        if st.button("📋 Quick Add Set"):
            # Quick add last exercise with same parameters
            if st.session_state["workout_rows"]:
                last_entry = st.session_state["workout_rows"][-1]
                if last_entry.get("set_type") == "normal":
                    rows = st.session_state["workout_rows"]
                    set_no = compute_next_set_no(rows, last_entry["exercise"])
                    new_entry = last_entry.copy()
                    new_entry["set_no"] = set_no
                    new_entry["timestamp"] = datetime.now().isoformat(timespec="seconds")
                    rows.append(new_entry)
                    st.success(f"Quick added set of {last_entry['exercise']}")
                else:
                    st.warning("Quick add only works for normal sets")
            else:
                st.warning("No previous entries to copy")

    st.subheader("Current workout")
    df_current = df_from_rows(st.session_state["workout_rows"], kind="strength")
    
    # Convert workout_date to proper date format for display
    if not df_current.empty:
        df_current_display = df_current.copy()
        df_current_display["workout_date"] = pd.to_datetime(df_current_display["workout_date"]).dt.date
        
        # Create a combined identifier for supersets and dropsets for better visualization
        df_current_display["set_identifier"] = ""
        for idx, row in df_current_display.iterrows():
            if row["set_type"] == "superset":
                df_current_display.at[idx, "set_identifier"] = f"{row['superset_group']}-{row['superset_part']}"
            elif row["set_type"] == "dropset":
                df_current_display.at[idx, "set_identifier"] = f"{row['dropset_group']}-Drop{row['drop_no']}"
            else:
                df_current_display.at[idx, "set_identifier"] = "Normal"
    else:
        df_current_display = df_current
    
    edited = st.data_editor(
        df_current_display,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "workout_date": st.column_config.DateColumn("Date"),
            "exercise": st.column_config.TextColumn("Exercise", width="medium"),
            "set_no": st.column_config.NumberColumn("Set #", step=1, width="small"),
            "weight": st.column_config.NumberColumn("Weight", step=0.5, width="small"),
            "weight_unit": st.column_config.SelectboxColumn("Unit", options=["kg", "lb"], width="small"),
            "reps": st.column_config.NumberColumn("Reps", step=1, width="small"),
            "rpe": st.column_config.NumberColumn("RPE", step=0.5, min_value=1.0, max_value=10.0, width="small"),
            "pain": st.column_config.NumberColumn("Pain", step=0.5, min_value=0.0, max_value=10.0, width="small"),
            "set_type": st.column_config.SelectboxColumn("Type", options=["normal","superset","dropset"], width="small"),
            "set_identifier": st.column_config.TextColumn("Set ID", help="Shows superset/dropset grouping", width="medium"),
            "superset_group": st.column_config.TextColumn("SS Group", width="small"),
            "superset_part": st.column_config.SelectboxColumn("SS Part", options=["","A","B","C","D"], width="small"),
            "dropset_group": st.column_config.TextColumn("DS Group", width="small"),
            "drop_no": st.column_config.NumberColumn("Drop #", step=1, width="small"),
            "notes": st.column_config.TextColumn("Notes", width="medium"),
        },
        hide_index=True,
        column_order=[
            "workout_date", "exercise", "set_no", "set_type", "set_identifier",
            "weight", "weight_unit", "reps", "rpe", "pain", "notes"
        ]
    )
    
    # Convert back to string format for consistency
    if not edited.empty:
        edited["workout_date"] = edited["workout_date"].astype(str)
        # Remove the helper column before saving
        if "set_identifier" in edited.columns:
            edited = edited.drop("set_identifier", axis=1)
    
    st.session_state["workout_rows"] = edited.to_dict(orient="records")
    
    # Display workout summary
    if st.session_state["workout_rows"]:
        st.markdown("### 📊 Workout Summary")
        
        insights = get_workout_insights(st.session_state["workout_rows"])
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total Sets", insights["total_sets"])
        with col2:
            st.metric("Exercises", insights["unique_exercises"])
        with col3:
            st.metric("Set Types", f"N:{insights['normal_sets']} S:{insights['superset_groups']} D:{insights['dropset_groups']}")
        with col4:
            st.metric("Est. Time", f"{insights['estimated_time_min']} min")
        
        # Show groupings
        if insights["superset_groups"] > 0 or insights["dropset_groups"] > 0:
            with st.expander("🔗 Set Groupings Details"):
                supersets = {}
                dropsets = {}
                
                for row in st.session_state["workout_rows"]:
                    if row.get("set_type") == "superset":
                        group = row.get("superset_group", "")
                        if group not in supersets:
                            supersets[group] = []
                        supersets[group].append(f"{row['exercise']} ({row['superset_part']})")
                    elif row.get("set_type") == "dropset":
                        group = row.get("dropset_group", "")
                        if group not in dropsets:
                            dropsets[group] = []
                        dropsets[group].append(f"{row['weight']}{row['weight_unit']} x {row['reps']}")
                
                if supersets:
                    st.markdown("**Supersets:**")
                    for group, exercises in supersets.items():
                        unique_exercises = list(dict.fromkeys(exercises))  # Remove duplicates while preserving order
                        st.markdown(f"- **{group}**: {' → '.join(unique_exercises)}")
                
                if dropsets:
                    st.markdown("**Dropsets:**")
                    for group, drops in dropsets.items():
                        st.markdown(f"- **{group}**: {' → '.join(drops)}")
    
    st.caption("💡 Tip: Edit any cell above before saving. Use 'Set ID' column to see superset/dropset groupings.")

    if st.button("💾 Save workout", type="primary", key="save_strength"):
        if not st.session_state["workout_rows"]:
            st.warning("No rows to save.")
        else:
            df_sets = df_from_rows(st.session_state["workout_rows"], kind="strength").copy()
            df_sets["session_name"] = session_name; df_sets["session_notes"] = notes_global
            df_out = merge_with_exercise_meta(df_sets, load_exercises())
            cols_first = [
                "workout_date","session_name","exercise","variant","set_no","weight","weight_unit","reps","rpe","pain",
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
        notes_c = create_notes_input("cardio_notes", "Notes (optional)")
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
            
            st.markdown("**Last week (" + str(last_week) + ")**")
            if not lw_pivot.empty and len(lw_pivot.columns) > 0:
                lw_sorted = lw_pivot.sort_values(by=lw_pivot.columns[0], ascending=False)
                st.dataframe(lw_sorted, use_container_width=True)
            else:
                st.info("No workout data for last week yet.")
            
            st.markdown("**Weekly mean (all weeks)**")
            if not mean_df.empty:
                st.dataframe(mean_df.sort_values("weekly_mean", ascending=False), use_container_width=True)
            else:
                st.info("No workout data available yet.")

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
            ).properties(height=300)
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
