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
Columns (case-insensitive): exercise, primary_muscle, secondary_muscle, tertiary_muscle, variant
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
import yaml
from dateutil import tz

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()  # This loads the .env file
except ImportError:
    pass  # dotenv not installed, skip loading

# ----------------- Session State Persistence -----------------
def save_session_state():
    """Save critical session state to browser's session storage via query params"""
    # Don't save if user just cleared everything intentionally
    if st.session_state.get('_intentionally_cleared', False):
        return
        
    # Save workout data to session state with a special key that persists across auth
    if 'workout_rows' in st.session_state and st.session_state['workout_rows']:
        st.session_state['_saved_workout_rows'] = st.session_state['workout_rows']
    if 'cardio_rows' in st.session_state and st.session_state['cardio_rows']:
        st.session_state['_saved_cardio_rows'] = st.session_state['cardio_rows']
    
    # Save form values
    persistent_keys = [
        'strength_date',
        'cardio_date', 'cardio_notes',
        'superset_counter', 'dropset_counter'
    ]
    
    for key in persistent_keys:
        if key in st.session_state:
            st.session_state[f'_saved_{key}'] = st.session_state[key]

def restore_session_state():
    """Restore session state after authentication"""
    # Don't restore if user intentionally cleared data
    if st.session_state.get('_intentionally_cleared', False):
        return
    
    # Restore workout data only if current data is empty
    if '_saved_workout_rows' in st.session_state and not st.session_state.get('workout_rows', []):
        st.session_state['workout_rows'] = st.session_state['_saved_workout_rows']
        # Show notification that data was restored
        st.toast("🔄 Restored your workout progress!", icon="✅")
    
    if '_saved_cardio_rows' in st.session_state and not st.session_state.get('cardio_rows', []):
        st.session_state['cardio_rows'] = st.session_state['_saved_cardio_rows']
    
    # Restore form values
    persistent_keys = [
        'strength_date',
        'cardio_date', 'cardio_notes',
        'superset_counter', 'dropset_counter'
    ]
    
    for key in persistent_keys:
        saved_key = f'_saved_{key}'
        if saved_key in st.session_state and key not in st.session_state:
            st.session_state[key] = st.session_state[saved_key]

def clear_saved_session_state():
    """Clear saved session state after successful save"""
    keys_to_clear = [key for key in st.session_state.keys() if key.startswith('_saved_')]
    for key in keys_to_clear:
        del st.session_state[key]
    # Clear the intentional clear flag as well
    if '_intentionally_cleared' in st.session_state:
        del st.session_state['_intentionally_cleared']

# ----------------- Config -----------------
DATA_DIR = "data"
EXERCISES_XLSX = os.path.join(DATA_DIR, "exercises.xlsx")
WORKOUTS_CSV = os.path.join(DATA_DIR, "workouts.csv")
CARDIO_CSV = os.path.join(DATA_DIR, "cardio.csv")

DEFAULT_CARDIO = ["Running", "Cycling", "Swimming", "Jump Rope", "Stairmaster", "Rowing", "Elliptical", "Walking", "Hiking", "Other"]

# Page config
st.set_page_config(page_title="Workout Tracker", page_icon="💪", layout="wide")

# ----------------- Mandatory Auth -----------------
def authenticate():
    # Save current session state before authentication check
    save_session_state()
    
    try:
        import streamlit_authenticator as stauth
    except ImportError as e:
        st.error(f"Auth dependencies not installed: {e}")
        st.stop()
    
    def deep_dict_convert(obj):
        """Recursively convert secrets objects to regular dicts"""
        if hasattr(obj, 'to_dict'):
            # If it has a to_dict method, use it
            return deep_dict_convert(obj.to_dict())
        elif hasattr(obj, '__dict__'):
            # Convert object with attributes to dict
            return {k: deep_dict_convert(v) for k, v in obj.__dict__.items()}
        elif isinstance(obj, dict):
            # Convert dict recursively
            return {k: deep_dict_convert(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            # Convert lists/tuples recursively
            return [deep_dict_convert(item) for item in obj]
        else:
            # Return primitive types as-is
            return obj
    
    # Try to load from secrets first, then fall back to local file
    config = None
    try:
        # Check if we have auth secrets configured
        if hasattr(st, 'secrets') and "auth" in st.secrets:
            # Deep convert all secrets to regular Python objects
            config = deep_dict_convert(st.secrets["auth"])
        else:
            # Fallback to local auth.yaml file
            cfg_path = "auth.yaml"
            if not os.path.exists(cfg_path):
                st.error("auth.yaml not found and no auth secrets configured.")
                st.error("For local development: Create auth.yaml file")
                st.error("For Streamlit Cloud: Configure secrets in app settings")
                st.stop()
            
            with open(cfg_path, "r") as f:
                config = yaml.safe_load(f)
    except Exception as e:
        st.error(f"Failed to load authentication config: {e}")
        st.error("Make sure .streamlit/secrets.toml exists or auth.yaml is configured")
        st.stop()
    
    if not config:
        st.error("No authentication configuration found")
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
        
        # Restore session state after successful authentication
        restore_session_state()
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
    
    # Add tertiary_muscle if it exists, otherwise create empty column
    if "tertiary_muscle" not in df.columns:
        df["tertiary_muscle"] = ""
        
    for col in ["exercise", "primary_muscle", "secondary_muscle", "tertiary_muscle", "variation"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    df = df[df["exercise"] != ""].drop_duplicates(subset=["exercise", "variation"]).sort_values(["exercise", "variation"])
    return df[["exercise", "primary_muscle", "secondary_muscle", "tertiary_muscle", "variation"]]


@st.cache_data(show_spinner=False)
def load_exercises(path: str = EXERCISES_XLSX) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            df = pd.read_excel(path)
            return normalize_exercise_df(df)
        except Exception as e:
            st.error(f"Couldn't read {path}: {e}")
            return pd.DataFrame(columns=["exercise", "primary_muscle", "secondary_muscle", "tertiary_muscle", "variation"])
    else:
        return pd.DataFrame(columns=["exercise", "primary_muscle", "secondary_muscle", "tertiary_muscle", "variation"])


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


def create_exercise_selection(df_ex: pd.DataFrame, key: str = None, body_part_filter: str = "All") -> str:
    """Create exercise selection with variation options and body part filtering"""
    if df_ex.empty:
        return ""
    
    # Filter exercises by body part
    if body_part_filter != "All":
        df_filtered = filter_exercises_by_body_part(df_ex, body_part_filter)
    else:
        df_filtered = df_ex
    
    if df_filtered.empty:
        st.warning(f"No exercises found for {body_part_filter} body part filter")
        return ""
    
    # Create exercise options with variations
    options = []
    for _, row in df_filtered.iterrows():
        base_exercise = row["exercise"]
        variations = get_exercise_variations(df_filtered, base_exercise)
        
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


def filter_exercises_by_body_part(df_ex: pd.DataFrame, body_part: str) -> pd.DataFrame:
    """Filter exercises by body part category"""
    if body_part == "All":
        return df_ex
    
    # Define muscle groups for each body part
    body_part_mapping = {
        "Upper": ["Chest", "Back", "Shoulders", "Biceps", "Triceps", "Lats", "Traps", "Front Delts", "Rear Delts", "Side Delts"],
        "Lower": ["Quads", "Hamstrings", "Glutes", "Calves", "Hip Flexors", "Adductors", "Abductors"],
        "Push": ["Chest", "Shoulders", "Triceps", "Front Delts", "Side Delts"],
        "Pull": ["Back", "Biceps", "Lats", "Traps", "Rear Delts"],
        "Full Body": []  # Will include exercises that target multiple body parts
    }
    
    if body_part not in body_part_mapping:
        return df_ex
    
    if body_part == "Full Body":
        # Full body includes compound movements that work both upper and lower
        upper_muscles = body_part_mapping["Upper"]
        lower_muscles = body_part_mapping["Lower"]
        
        # Find exercises that have muscles from both upper and lower body
        mask = df_ex.apply(lambda row: 
            any(muscle in upper_muscles for muscle in [row.get("primary_muscle", ""), row.get("secondary_muscle", ""), row.get("tertiary_muscle", "")] if muscle) and
            any(muscle in lower_muscles for muscle in [row.get("primary_muscle", ""), row.get("secondary_muscle", ""), row.get("tertiary_muscle", "")] if muscle),
            axis=1
        )
        return df_ex[mask]
    else:
        target_muscles = body_part_mapping[body_part]
        
        # Filter exercises where primary, secondary, or tertiary muscle is in target muscles
        mask = df_ex.apply(lambda row:
            any(row.get(muscle_col, "") in target_muscles 
                for muscle_col in ["primary_muscle", "secondary_muscle", "tertiary_muscle"]),
            axis=1
        )
        return df_ex[mask]


def create_notes_input(key_prefix: str, placeholder: str = "Notes (optional)") -> str:
    """Create notes input with predefined options"""
    col1, col2 = st.columns([2, 1])
    
    with col2:
        quick_note = st.selectbox(
            "Quick notes", 
            ["Standard", "Rehab", "Deload", "Other gym"], 
            key=f"{key_prefix}_quick"
        )
    
    with col1:
        custom_note = st.text_input(placeholder, key=f"{key_prefix}_custom")
    
    # Combine notes
    notes = []
    if quick_note not in ["Standard", "None"]:  # Don't include "Standard" as it's the default
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


def create_exercise_variant_inputs(df_ex: pd.DataFrame, key_prefix: str = "", body_part_filter: str = "All"):
    """Create separate exercise and variant dropdowns that update dynamically"""
    col1, col2 = st.columns([2, 1])
    
    # Filter exercises by body part
    if body_part_filter != "All":
        df_filtered = filter_exercises_by_body_part(df_ex, body_part_filter)
    else:
        df_filtered = df_ex
    
    with col1:
        exercise = st.selectbox(
            "Exercise", 
            options=df_filtered["exercise"].unique().tolist() if not df_filtered.empty else [],
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


def create_weight_input(variant: str, key_prefix: str = ""):
    """Create weight input that adapts based on variant (bands vs numerical)"""
    is_bands = variant.lower() == "bands"
    
    if is_bands:
        # Band resistance system
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            resistance_level = st.selectbox(
                "Resistance Level",
                options=["Ultra-Light", "Light", "Medium", "Heavy", "Ultra-Heavy"],
                index=2,  # Default to Medium
                key=f"{key_prefix}_resistance" if key_prefix else "resistance"
            )
        
        with col2:
            add_bodyweight = st.checkbox(
                "Add bodyweight",
                key=f"{key_prefix}_add_bw" if key_prefix else "add_bw"
            )
        
        with col3:
            additional_weight = st.number_input(
                "Additional weight",
                min_value=0.0,
                step=0.5,
                format="%.2f",
                value=0.0,
                help="Extra weight added (plates, dumbbells, etc.)",
                key=f"{key_prefix}_add_weight" if key_prefix else "add_weight"
            )
        
        # Create weight description and numeric value
        weight_description = resistance_level
        if add_bodyweight:
            weight_description += " + BW"
        if additional_weight > 0:
            weight_description += f" + {additional_weight}kg"
        
        return {
            "weight": additional_weight,  # Numeric component for calculations
            "weight_type": "Bands",
            "resistance_level": resistance_level,
            "add_bodyweight": add_bodyweight,
            "weight_description": weight_description
        }
    else:
        # Standard numerical weight
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            weight = st.number_input(
                "Weight", 
                min_value=0.0, 
                step=0.5, 
                format="%.2f",
                key=f"{key_prefix}_weight" if key_prefix else "weight"
            )
        
        with col2:
            weight_unit = st.radio(
                "Unit", 
                options=["kg", "lb"], 
                horizontal=True,
                key=f"{key_prefix}_weight_unit" if key_prefix else "weight_unit"
            )
        
        with col3:
            add_bodyweight = st.checkbox(
                "Add bodyweight",
                key=f"{key_prefix}_add_bw" if key_prefix else "add_bw"
            )
        
        # Create weight description
        weight_description = f"{weight}{weight_unit}"
        if add_bodyweight:
            weight_description += " + BW"
        
        return {
            "weight": weight,
            "weight_type": "Standard",
            "resistance_level": "",
            "add_bodyweight": add_bodyweight,
            "weight_description": weight_description,
            "weight_unit": weight_unit
        }


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
            
            # Debug: Show available columns
            if combined_df.empty:
                st.warning("⚠️ Workout history loaded but contains no data")
            else:
                st.sidebar.info(f"📊 Loaded {len(combined_df)} workout entries")
                # Show columns for debugging
                if st.sidebar.checkbox("Show data columns (debug)", value=False):
                    st.sidebar.write("Available columns:", list(combined_df.columns))
                    
            return combined_df
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Error loading workout history: {e}")
        # Also show in sidebar for debugging
        st.sidebar.error(f"Workout history load failed: {str(e)[:50]}...")
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
    date_col = None
    if 'workout_date' in filtered_df.columns:
        date_col = 'workout_date'
    elif 'date' in filtered_df.columns:
        date_col = 'date'
    
    if date_col:
        filtered_df = filtered_df.sort_values(date_col, ascending=False)
    
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
                display_cols = ['workout_date', 'date', 'exercise', 'variant', 'weight', 'reps', 'sets', 'rpe', 'notes']
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
                date_col = 'workout_date' if 'workout_date' in history_results.columns else 'date'
                if date_col in history_results.columns:
                    latest_entries = history_results.groupby(['exercise', 'variant']).first().reset_index()
                else:
                    latest_entries = history_results.groupby(['exercise', 'variant']).last().reset_index()
                
                display_cols = ['exercise', 'variant', 'workout_date', 'date', 'weight', 'reps', 'sets', 'rpe']
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
            "workout_date","exercise","variant","set_no","weight","weight_unit","weight_type",
            "resistance_level","add_bodyweight","weight_description","reps","rpe","pain","set_type",
            "superset_group","superset_part","dropset_group","drop_no","timestamp","notes"
        ]
    else:
        cols = [
            "workout_date","activity","duration_min","distance_km","rpe","pain","heat_training","heat_duration_min","timestamp","notes"
        ]
    if not rows:
        return pd.DataFrame(columns=cols)
    
    # Create DataFrame and ensure all columns exist
    df = pd.DataFrame(rows)
    for col in cols:
        if col not in df.columns:
            if col in ["variant", "notes", "superset_group", "superset_part", "dropset_group", "weight_unit", "weight_type", "resistance_level", "weight_description"]:
                df[col] = ""
            elif col in ["weight", "reps", "rpe", "pain", "set_no", "drop_no", "heat_duration_min"]:
                df[col] = 0
            elif col in ["add_bodyweight", "heat_training"]:
                df[col] = False
            else:
                df[col] = ""
    
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
        if hasattr(st, 'secrets') and "github" in st.secrets:
            github_secrets = st.secrets["github"]
            token = github_secrets.get("token")
            repo = github_secrets.get("repo")
            branch = github_secrets.get("branch", "main")
            path_strength = github_secrets.get("path_strength", "data/workouts.csv")
            path_cardio = github_secrets.get("path_cardio", "data/cardio.csv")
        else:
            # Fallback to environment variables
            token = os.environ.get("GITHUB_TOKEN")
            repo = os.environ.get("GITHUB_REPO")
            branch = os.environ.get("GITHUB_BRANCH", "main")
            path_strength = os.environ.get("GITHUB_FILEPATH_STRENGTH", "data/workouts.csv")
            path_cardio = os.environ.get("GITHUB_FILEPATH_CARDIO", "data/cardio.csv")
        
        return token, repo, branch, path_strength, path_cardio
    except Exception:
        # Final fallback to environment variables only
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


def save_csv_github_replace(df_new: pd.DataFrame, which: str):
    """Replace the entire CSV file on GitHub with new data"""
    token, repo, branch, path_strength, path_cardio = github_env()
    if which == "strength":
        path = path_strength
    else:
        path = path_cardio
    if not (token and repo and path and branch):
        return False, "GitHub env vars not fully set; skipped GitHub save."

    # Convert dataframe to CSV
    csv_buf = io.StringIO()
    df_new.to_csv(csv_buf, index=False)
    b64 = base64.b64encode(csv_buf.getvalue().encode("utf-8")).decode("utf-8")
    
    # Check if file exists to get SHA for update
    meta = github_get_file(token, repo, path, ref=branch)
    if meta is None:
        # File doesn't exist, create it
        github_put_file(token, repo, path, b64, message=f"Create {os.path.basename(path)}", branch=branch)
        return True, f"Created {path} in {repo}@{branch}"
    else:
        # File exists, replace it
        github_put_file(token, repo, path, b64, message="Replace data file", branch=branch, sha=meta.get("sha"))
        return True, f"Replaced {path} in {repo}@{branch}"

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
    
    # Progress preservation section
    st.divider()
    st.subheader("💾 Progress Protection")
    
    # Show current progress status
    workout_count = len(st.session_state.get('workout_rows', []))
    cardio_count = len(st.session_state.get('cardio_rows', []))
    saved_workout_count = len(st.session_state.get('_saved_workout_rows', []))
    saved_cardio_count = len(st.session_state.get('_saved_cardio_rows', []))
    
    if workout_count > 0 or cardio_count > 0:
        st.info(f"📝 Current: {workout_count} strength, {cardio_count} cardio entries")
    
    if saved_workout_count > 0 or saved_cardio_count > 0:
        st.success(f"🛡️ Protected: {saved_workout_count} strength, {saved_cardio_count} cardio entries")
    
    if workout_count == 0 and cardio_count == 0 and saved_workout_count == 0 and saved_cardio_count == 0:
        st.caption("No entries to protect yet")
    
    # Show auto-save status
    if st.session_state.get('_intentionally_cleared', False):
        st.warning("⚠️ Auto-save disabled (you cleared data)")
    else:
        st.info("✅ Auto-save active")
    
    # Manual save progress button
    if st.button("🛡️ Save Progress", help="Manually save your current progress to protect against logouts"):
        save_session_state()
        st.success("Progress saved! Your data is now protected.")
    
    # Clear saved progress button (for debugging/cleanup)
    if saved_workout_count > 0 or saved_cardio_count > 0:
        if st.button("🗑️ Clear Protected Data", help="Clear saved progress data"):
            clear_saved_session_state()
            st.session_state['_intentionally_cleared'] = True  # Prevent immediate restoration
            st.success("Protected data cleared.")
    
    # Resume auto-save button if it was disabled
    if st.session_state.get('_intentionally_cleared', False):
        if st.button("🔄 Resume Auto-Save", help="Resume automatic progress protection"):
            del st.session_state['_intentionally_cleared']
            st.success("Auto-save resumed! Your progress will be protected again.")

# ----------------- Tabs -----------------
strength_tab, cardio_tab, analytics_tab, data_tab = st.tabs(["🏋️ Strength", "🏃 Cardio", "📈 Analytics", "📊 Data Manager"])

# ===== Strength tab =====
with strength_tab:
    st.session_state.setdefault("workout_rows", [])
    st.session_state.setdefault("superset_counter", 1)
    st.session_state.setdefault("dropset_counter", 1)

    c1, c2 = st.columns([1,2])
    with c1:
        workout_date = st.date_input("Workout date", value=today_local_date(), key="strength_date")
    st.markdown("---")

    # Add exercise history search
    df_history = load_workout_history()
    if not df_history.empty:
        display_exercise_history_search(df_history, df_ex)

    # Body part filter and previous workout loader
    st.markdown("---")
    st.subheader("🎯 Workout Setup")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("**Exercise Filter**")
        body_part_filter = st.selectbox(
            "Filter exercises by body part",
            ["All", "Upper", "Lower", "Push", "Pull", "Full Body"],
            help="Filter available exercises by movement pattern or body region"
        )
        
        # Show muscle groups for selected filter
        if body_part_filter != "All":
            muscle_info = {
                "Upper": "Chest, Back, Shoulders, Biceps, Triceps, Lats, Traps",
                "Lower": "Quads, Hamstrings, Glutes, Calves, Hip Flexors",
                "Push": "Chest, Shoulders, Triceps, Front/Side Delts",
                "Pull": "Back, Biceps, Lats, Traps, Rear Delts",
                "Full Body": "Compound movements (Upper + Lower)"
            }
            st.caption(f"*Includes: {muscle_info.get(body_part_filter, '')}*")
    
    with col2:
        st.markdown("**Previous Workout**")
        
        # Get available previous workouts
        if not df_history.empty:
            # Check if required columns exist
            if 'workout_date' not in df_history.columns:
                st.warning("⚠️ Workout history data missing 'workout_date' column")
                st.info("Available columns: " + ", ".join(df_history.columns.tolist()))
                unique_dates = []  # No dates available
            else:
                # Ensure workout_date is datetime
                df_history['workout_date'] = pd.to_datetime(df_history['workout_date'], errors='coerce')
                
                # Get unique workout dates (last 5)
                unique_dates = df_history['workout_date'].dropna().dt.date.unique()
                unique_dates = sorted(unique_dates, reverse=True)[:5]  # Last 5 workouts
            
            if unique_dates:
                # Convert dates to strings for selectbox
                date_options = [date.strftime('%Y-%m-%d') for date in unique_dates]
                
                selected_date_str = st.selectbox(
                    "Choose workout to load",
                    options=date_options,
                    help="Select from your last 5 workout dates"
                )
                
                # Show preview of selected workout
                selected_date = pd.to_datetime(selected_date_str).date()
                selected_workout = df_history[df_history['workout_date'].dt.date == selected_date]
                
                # Safely count exercises and sets
                if 'exercise' in selected_workout.columns:
                    exercise_count = selected_workout['exercise'].nunique()
                    set_count = len(selected_workout)
                    st.caption(f"*{exercise_count} exercises, {set_count} sets*")
                else:
                    set_count = len(selected_workout)
                    st.caption(f"*{set_count} entries*")
                
                if st.button("📋 Load Selected Workout", help="Load the selected workout into current session"):
                    selected_workout_data = df_history[df_history['workout_date'].dt.date == selected_date].copy()
                    
                    if not selected_workout_data.empty:
                        # Convert to the format expected by workout_rows
                        loaded_rows = []
                        for _, row in selected_workout_data.iterrows():
                            # Helper function to safely convert values
                            def safe_int(value, default=0):
                                try:
                                    if pd.isna(value):
                                        return default
                                    return int(float(value))
                                except (ValueError, TypeError):
                                    return default
                            
                            def safe_float(value, default=0.0):
                                try:
                                    if pd.isna(value):
                                        return default
                                    return float(value)
                                except (ValueError, TypeError):
                                    return default
                            
                            def safe_bool(value, default=False):
                                try:
                                    if pd.isna(value):
                                        return default
                                    return bool(value)
                                except (ValueError, TypeError):
                                    return default
                            
                            workout_row = {
                                "workout_date": workout_date.isoformat(),  # Use current selected date
                                "exercise": str(row.get("exercise", "")).strip(),
                                "variant": str(row.get("variant", "")).strip(),
                                "set_no": safe_int(row.get("set_no"), 1),
                                "weight": safe_float(row.get("weight"), 0),
                                "weight_unit": str(row.get("weight_unit", "kg")).strip(),
                                "weight_type": str(row.get("weight_type", "Standard")).strip(),
                                "resistance_level": str(row.get("resistance_level", "")).strip(),
                                "add_bodyweight": safe_bool(row.get("add_bodyweight"), False),
                                "weight_description": str(row.get("weight_description", "")).strip(),
                                "reps": safe_int(row.get("reps"), 0),
                                "rpe": safe_float(row.get("rpe"), 6.0),
                                "pain": safe_float(row.get("pain"), 0.0),
                                "set_type": str(row.get("set_type", "normal")).strip(),
                                "superset_group": str(row.get("superset_group", "")).strip(),
                                "superset_part": str(row.get("superset_part", "")).strip(),
                                "dropset_group": str(row.get("dropset_group", "")).strip(),
                                "drop_no": safe_int(row.get("drop_no"), None) if not pd.isna(row.get("drop_no")) else None,
                                "timestamp": datetime.now().isoformat(timespec="seconds"),
                                "notes": str(row.get("notes", "")).strip(),
                            }
                            loaded_rows.append(workout_row)
                        
                        # Add to current workout
                        st.session_state["workout_rows"].extend(loaded_rows)
                        
                        st.success(f"✅ Loaded {len(loaded_rows)} sets from {selected_date_str}")
                        st.balloons()
                        save_session_state()  # Save the loaded workout
                    else:
                        st.warning("No workout data found for selected date")
            else:
                st.info("No previous workouts available")
        else:
            st.info("No workout history available")

    st.subheader("Add set(s)")
    
    # Set type selection at the top
    col1, col2 = st.columns([3, 1])
    with col1:
        set_type = st.radio("Set Type", ["Normal Sets", "Superset", "Dropset"], horizontal=True, key="set_type_selection")
    with col2:
        if st.button("📝 Clear Notes", help="Clear all notes fields and reset quick notes to 'Standard'"):
            # Clear only notes-related fields - use a more comprehensive approach
            notes_keys_to_clear = []
            
            # Get all keys that contain notes
            for key in st.session_state.keys():
                if ('_notes_' in key or key.endswith('_notes')) and not key.startswith('_saved_'):
                    notes_keys_to_clear.append(key)
            
            # Clear found keys and reset quick notes to "Standard"
            for key in notes_keys_to_clear:
                if key.endswith("_quick"):
                    st.session_state[key] = "Standard"
                elif key.endswith("_custom"):
                    st.session_state[key] = ""
                else:
                    if key in st.session_state:
                        del st.session_state[key]
            
            # Also clear any specific known keys that might not match the pattern
            specific_keys = [
                "normal_notes_quick", "normal_notes_custom",
                "dropset_notes_quick", "dropset_notes_custom", 
                "cardio_notes_quick", "cardio_notes_custom"
            ]
            
            for key in specific_keys:
                if key.endswith("_quick"):
                    st.session_state[key] = "Standard"
                elif key.endswith("_custom"):
                    st.session_state[key] = ""
            
            # Add superset notes clearing
            for i in range(4):
                st.session_state[f"ss_notes_{i}_quick"] = "Standard"
                st.session_state[f"ss_notes_{i}_custom"] = ""
            
            st.success("Notes cleared and quick notes set to 'Standard'!")
            st.rerun()
    
    if set_type == "Normal Sets":
        # Exercise selection outside form for dynamic updates
        exercise, variant = create_exercise_variant_inputs(df_ex, "normal", body_part_filter)
        
        with st.form("add_normal_sets_form", clear_on_submit=False):
            # Dynamic weight input based on variant
            weight_info = create_weight_input(variant, "normal")
            
            c5, c6, c7 = st.columns(3)
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
                    # For bands, weight can be 0; for standard weights, must be > 0
                    if weight_info["weight_type"] == "Standard" and weight_info["weight"] <= 0:
                        st.error("Weight must be greater than 0 for standard weights")
                    elif reps <= 0:
                        st.error("Reps must be greater than 0")
                    else:
                        rows = st.session_state["workout_rows"]
                        for i in range(int(sets_to_add)):
                            set_no = compute_next_set_no(rows, exercise)
                            row_data = {
                                "workout_date": workout_date.isoformat(),
                                "exercise": exercise,
                                "variant": variant.strip() if variant else "",
                                "set_no": set_no,
                                "weight": float(weight_info["weight"]),
                                "weight_unit": weight_info.get("weight_unit", "kg"),
                                "weight_type": weight_info["weight_type"],
                                "resistance_level": weight_info["resistance_level"],
                                "add_bodyweight": weight_info["add_bodyweight"],
                                "weight_description": weight_info["weight_description"],
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
                            }
                            rows.append(row_data)
                        st.success(f"Added {int(sets_to_add)} set(s) of {exercise}.")
                        save_session_state()  # Auto-save progress after adding sets
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
                
                # Exercise selection
                ex, ex_variant = create_exercise_variant_inputs(df_ex, key_prefix=f"ss_{i}", body_part_filter=body_part_filter)
                
                # Dynamic weight input based on variant
                weight_info = create_weight_input(ex_variant, f"ss_{i}")
                
                col3, col4, col5 = st.columns([1, 1, 1])
                with col3:
                    r = st.number_input(f"Reps", min_value=1, step=1, value=8, key=f"ss_reps_{i}")
                with col4:
                    rpe_val = st.number_input(f"RPE", min_value=1.0, max_value=10.0, step=0.5, value=8.5, format="%.1f", key=f"ss_rpe_{i}")
                with col5:
                    pain_val = st.number_input(f"Pain", min_value=0.0, max_value=10.0, step=0.5, value=0.0, format="%.1f", key=f"ss_pain_{i}")
                
                notes_ex = create_notes_input(f"ss_notes_{i}", f"Notes for {ex if ex else 'Exercise'} (optional)")
                
                superset_exercises.append({
                    "exercise": ex,
                    "variant": ex_variant.strip() if ex_variant else "",
                    "weight_info": weight_info,
                    "reps": r,
                    "rpe": rpe_val,
                    "pain": pain_val,
                    "notes": notes_ex
                })
            
            rounds = st.number_input("Number of superset rounds", min_value=1, max_value=10, value=1)
            
            submitted_ss = st.form_submit_button("➕ Add Complete Superset")
            
            if submitted_ss:
                if not df_ex.empty and all(ex["exercise"] for ex in superset_exercises):
                    # Validate all exercises - for bands, weight can be 0; for standard weights, must be > 0
                    valid = True
                    for ex in superset_exercises:
                        weight_info = ex["weight_info"]
                        if weight_info["weight_type"] == "Standard" and weight_info["weight"] <= 0:
                            st.error(f"Standard weights must be > 0 for all exercises")
                            valid = False
                            break
                        elif ex["reps"] <= 0:
                            st.error(f"All exercises must have reps > 0")
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
                                weight_info = ex["weight_info"]
                                rows.append({
                                    "workout_date": workout_date.isoformat(),
                                    "exercise": ex["exercise"],
                                    "variant": ex["variant"],
                                    "set_no": set_no,
                                    "weight": float(weight_info["weight"]),
                                    "weight_unit": weight_info.get("weight_unit", "kg"),
                                    "weight_type": weight_info["weight_type"],
                                    "resistance_level": weight_info["resistance_level"],
                                    "add_bodyweight": weight_info["add_bodyweight"],
                                    "weight_description": weight_info["weight_description"],
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
                        save_session_state()  # Auto-save progress after adding superset
                else:
                    st.warning("Please select all exercises and ensure exercises file is loaded.")

    elif set_type == "Dropset":
        # Exercise selection outside form for dynamic updates
        st.markdown("**Configure Dropset** (Same exercise with decreasing weight/resistance)")
        
        # Exercise selection
        exercise, variant = create_exercise_variant_inputs(df_ex, "dropset", body_part_filter)
        
        with st.form("add_dropset_form", clear_on_submit=False):
            # Dropset group name
            dropset_group = st.text_input("Dropset Name", value="", placeholder=f"Auto: {get_next_dropset_name()}")
            
            # Number of drops
            num_drops = st.number_input("Number of weight drops", min_value=2, max_value=6, value=3)
            
            st.markdown("**Configure each drop:**")
            
            dropset_drops = []
            is_bands = variant.lower() == "bands"
            
            for i in range(num_drops):
                st.markdown(f"**Drop {i+1}:**")
                
                if is_bands:
                    # Band resistance system for dropsets
                    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
                    
                    with col1:
                        resistance_levels = ["Ultra-Light", "Light", "Medium", "Heavy", "Ultra-Heavy"]
                        # Default to decreasing resistance
                        default_idx = max(0, min(len(resistance_levels)-1, 4-i))
                        resistance_level = st.selectbox(
                            f"Resistance Level",
                            options=resistance_levels,
                            index=default_idx,
                            key=f"ds_resistance_{i}"
                        )
                    
                    with col2:
                        add_bw = st.checkbox(f"+ BW", key=f"ds_bw_{i}")
                    
                    with col3:
                        add_weight = st.number_input(f"+ Weight", min_value=0.0, step=0.5, format="%.2f", value=0.0, key=f"ds_add_weight_{i}")
                    
                    with col4:
                        r = st.number_input(f"Reps", min_value=1, step=1, value=max(8-i, 3), key=f"ds_reps_{i}")
                    
                    with col5:
                        rpe_val = st.number_input(f"RPE", min_value=1.0, max_value=10.0, step=0.5, value=8.5+i*0.5, format="%.1f", key=f"ds_rpe_{i}")
                    
                    pain_val = st.number_input(f"Pain", min_value=0.0, max_value=10.0, step=0.5, value=0.0, format="%.1f", key=f"ds_pain_{i}")
                    
                    # Create weight description
                    weight_desc = resistance_level
                    if add_bw:
                        weight_desc += " + BW"
                    if add_weight > 0:
                        weight_desc += f" + {add_weight}kg"
                    
                    dropset_drops.append({
                        "weight": add_weight,
                        "weight_type": "Bands",
                        "resistance_level": resistance_level,
                        "add_bodyweight": add_bw,
                        "weight_description": weight_desc,
                        "reps": r,
                        "rpe": rpe_val,
                        "pain": pain_val
                    })
                    
                else:
                    # Standard weight dropset
                    col1, col2, col3, col4, col5 = st.columns([1, 1, 1, 1, 1])
                    
                    with col1:
                        w = st.number_input(f"Weight", min_value=0.0, step=0.5, format="%.2f", key=f"ds_weight_{i}")
                    with col2:
                        add_bw = st.checkbox(f"+ BW", key=f"ds_bw_{i}")
                    with col3:
                        r = st.number_input(f"Reps", min_value=1, step=1, value=max(8-i, 3), key=f"ds_reps_{i}")
                    with col4:
                        rpe_val = st.number_input(f"RPE", min_value=1.0, max_value=10.0, step=0.5, value=8.5+i*0.5, format="%.1f", key=f"ds_rpe_{i}")
                    with col5:
                        pain_val = st.number_input(f"Pain", min_value=0.0, max_value=10.0, step=0.5, value=0.0, format="%.1f", key=f"ds_pain_{i}")
                    
                    # Create weight description
                    weight_desc = f"{w}kg"
                    if add_bw:
                        weight_desc += " + BW"
                    
                    dropset_drops.append({
                        "weight": w,
                        "weight_type": "Standard",
                        "resistance_level": "",
                        "add_bodyweight": add_bw,
                        "weight_description": weight_desc,
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
                    # Validate all drops - different logic for bands vs standard weights
                    valid = True
                    is_bands = variant.lower() == "bands"
                    
                    for i, drop in enumerate(dropset_drops):
                        if drop["reps"] <= 0:
                            st.error(f"All drops must have reps > 0")
                            valid = False
                            break
                        
                        if not is_bands:
                            # For standard weights, validate decreasing weight
                            if drop["weight"] <= 0:
                                st.error(f"Standard weights must be > 0 for all drops")
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
                                    "weight_unit": "kg",  # Default unit
                                    "weight_type": drop["weight_type"],
                                    "resistance_level": drop["resistance_level"],
                                    "add_bodyweight": drop["add_bodyweight"],
                                    "weight_description": drop["weight_description"],
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
                        save_session_state()  # Auto-save progress after adding dropset
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
            st.session_state['_intentionally_cleared'] = True  # Flag to prevent restoration
            clear_saved_session_state()  # Clear any saved data too
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
                    save_session_state()  # Auto-save progress after quick add
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
            "variant": st.column_config.TextColumn("Variant", width="small"),
            "set_no": st.column_config.NumberColumn("Set #", step=1, width="small"),
            "weight_description": st.column_config.TextColumn("Weight/Resistance", width="medium", help="Shows weight or band resistance"),
            "weight": st.column_config.NumberColumn("Weight (num)", step=0.5, width="small", help="Numeric weight value"),
            "weight_unit": st.column_config.SelectboxColumn("Unit", options=["kg", "lb"], width="small"),
            "weight_type": st.column_config.SelectboxColumn("Type", options=["Standard", "Bands"], width="small"),
            "resistance_level": st.column_config.TextColumn("Resistance", width="small"),
            "add_bodyweight": st.column_config.CheckboxColumn("+ BW", width="small"),
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
            "workout_date", "exercise", "variant", "set_no", "set_type", "set_identifier",
            "weight_description", "reps", "rpe", "pain", "notes"
        ]
    )
    
    # Convert back to string format for consistency
    if not edited.empty:
        edited["workout_date"] = edited["workout_date"].astype(str)
        # Remove the helper column before saving
        if "set_identifier" in edited.columns:
            edited = edited.drop("set_identifier", axis=1)
    
    st.session_state["workout_rows"] = edited.to_dict(orient="records")
    
    # Add specific entry deletion interface
    if st.session_state["workout_rows"]:
        with st.expander("🗑️ Delete Specific Entries"):
            st.markdown("**Select entries to delete:**")
            
            # Create a list of entries with descriptive labels
            entry_options = []
            for i, row in enumerate(st.session_state["workout_rows"]):
                set_type = row.get("set_type", "normal")
                if set_type == "superset":
                    label = f"Set {i+1}: {row['exercise']} - {row['superset_group']}-{row['superset_part']} ({row['weight']}{row['weight_unit']} x {row['reps']})"
                elif set_type == "dropset":
                    label = f"Set {i+1}: {row['exercise']} - {row['dropset_group']}-Drop{row['drop_no']} ({row['weight']}{row['weight_unit']} x {row['reps']})"
                else:
                    label = f"Set {i+1}: {row['exercise']} - Set {row['set_no']} ({row['weight']}{row['weight_unit']} x {row['reps']})"
                entry_options.append((i, label))
            
            # Multi-select for entries to delete
            entries_to_delete = st.multiselect(
                "Choose entries to delete:",
                options=[i for i, _ in entry_options],
                format_func=lambda x: next(label for i, label in entry_options if i == x),
                help="Select one or more entries to delete from your workout"
            )
            
            if entries_to_delete:
                col1, col2 = st.columns([1, 3])
                with col1:
                    if st.button("🗑️ Delete Selected", type="secondary"):
                        # Sort indices in reverse order to avoid index shifting issues
                        for idx in sorted(entries_to_delete, reverse=True):
                            del st.session_state["workout_rows"][idx]
                        
                        save_session_state()  # Auto-save after deletion
                        st.success(f"Deleted {len(entries_to_delete)} entries")
                        st.rerun()
                
                with col2:
                    st.info(f"Selected {len(entries_to_delete)} entries for deletion")
    
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
    
    st.caption("💡 Tip: Edit any cell above before saving. Use 'Set ID' column to see superset/dropset groupings. Use the 'Delete Specific Entries' section below to remove individual sets. For bands, select 'Bands' as variant to access resistance levels. Use 'Add bodyweight' checkbox for bodyweight exercises.")

    if st.button("💾 Save workout", type="primary", key="save_strength"):
        if not st.session_state["workout_rows"]:
            st.warning("No rows to save.")
        else:
            df_sets = df_from_rows(st.session_state["workout_rows"], kind="strength").copy()
            df_out = merge_with_exercise_meta(df_sets, load_exercises())
            cols_first = [
                "workout_date","exercise","variant","set_no","weight","weight_unit","weight_type",
                "resistance_level","add_bodyweight","weight_description","reps","rpe","pain",
                "set_type","superset_group","superset_part","dropset_group","drop_no","timestamp","notes",
                "primary_muscle","secondary_muscle","tertiary_muscle"
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
                    clear_saved_session_state()  # Clear saved session state after successful save
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
        
        # Heat training section
        st.markdown("**Heat Training** (optional)")
        h1, h2 = st.columns([1, 2])
        with h1:
            heat_training = st.checkbox("Heat training", value=False)
        with h2:
            heat_duration = st.number_input("Heat duration (min)", min_value=0.0, step=1.0, value=0.0, format="%.1f", disabled=not heat_training)
        
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
                    "heat_training": heat_training,
                    "heat_duration_min": float(heat_duration) if heat_training else 0.0,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "notes": notes_c.strip(),
                })
                st.success(f"Added {act} entry.")
                save_session_state()  # Auto-save progress after adding cardio entry

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
                    clear_saved_session_state()  # Clear saved session state after successful save
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
    # Calendar heatmap showing workout activity
    st.subheader("📅 Workout Activity Calendar")
    
    def create_workout_calendar():
        """Create a calendar heatmap showing workout activity by date"""
        # Load both strength and cardio data
        df_strength = load_strength()
        df_cardio = load_cardio()
        
        # Get unique workout dates
        strength_dates = set()
        cardio_dates = set()
        
        if not df_strength.empty:
            strength_dates = set(df_strength['workout_date'].dt.date)
        
        if not df_cardio.empty:
            cardio_dates = set(df_cardio['workout_date'].dt.date)
        
        # Create date range for the last 12 months
        end_date = pd.Timestamp.today().date()
        start_date = end_date - pd.Timedelta(days=365)
        
        # Generate all dates in range
        date_range = pd.date_range(start=start_date, end=end_date, freq='D')
        
        # Create calendar data
        calendar_data = []
        for date in date_range:
            date_only = date.date()
            has_strength = date_only in strength_dates
            has_cardio = date_only in cardio_dates
            
            if has_strength and has_cardio:
                activity_type = "Both"
                color = "#8B5CF6"  # Purple
            elif has_strength:
                activity_type = "Strength"
                color = "#EF4444"  # Red
            elif has_cardio:
                activity_type = "Cardio"
                color = "#3B82F6"  # Blue
            else:
                activity_type = "None"
                color = "#F3F4F6"  # Light gray
            
            calendar_data.append({
                'date': date,
                'activity_type': activity_type,
                'color': color,
                'day_of_week': date.day_name(),
                'week_of_year': date.isocalendar()[1],
                'day_of_month': date.day,
                'month': date.strftime('%Y-%m')
            })
        
        df_calendar = pd.DataFrame(calendar_data)
        
        if df_calendar.empty:
            st.info("No workout data available for calendar.")
            return
        
        # Create the heatmap chart
        base_chart = alt.Chart(df_calendar).mark_rect(
            stroke='white',
            strokeWidth=1
        ).encode(
            x=alt.X('date(date):O', title='Day', axis=alt.Axis(format='%e', labelAngle=0)),
            y=alt.Y('day(date):O', title='', axis=alt.Axis(format='%a')),
            color=alt.Color(
                'color:N',
                scale=None,  # Use the exact colors we specify
                legend=None
            ),
            tooltip=[
                alt.Tooltip('date:T', format='%Y-%m-%d', title='Date'),
                alt.Tooltip('activity_type:N', title='Activity'),
                alt.Tooltip('day_of_week:N', title='Day')
            ]
        ).properties(
            width=60,
            height=120
        )
        
        chart = base_chart.facet(
            column=alt.Column(
                'yearmonth(date):O',
                title='',
                header=alt.Header(
                    format='%B %Y',
                    labelAngle=0,
                    labelAlign='center'
                )
            )
        ).resolve_scale(
            x='independent'
        )
        
        st.altair_chart(chart, use_container_width=True)
        
        # Add legend
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown("🔴 **Strength only**")
        with col2:
            st.markdown("🔵 **Cardio only**")
        with col3:
            st.markdown("🟣 **Both activities**")
        with col4:
            st.markdown("⚪ **Rest day**")
    
    # Helper functions for loading data (defined here for use in calendar)
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
    
    # Display the calendar
    create_workout_calendar()
    
    st.divider()
    st.subheader("Strength volume by muscle group")
    metric = st.selectbox("Volume metric", ["Sets","Reps","Tonnage (Weight×Reps)"])
    week_ending = st.selectbox("Week ends on", ["Mon","Sun"], index=0)

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
            tert = str(r.get("tertiary_muscle", "")).strip()
            
            # Primary muscle gets full volume
            if prim and prim.lower() != "none":
                rows.append({"date": d, "muscle": prim, "volume": vol * 1.0})
            
            # Secondary muscle gets half volume (only if not "None")
            if sec and sec.lower() != "none":
                rows.append({"date": d, "muscle": sec, "volume": vol * 0.5})
            
            # Tertiary muscle gets one-third volume (only if not "None")
            if tert and tert.lower() != "none":
                rows.append({"date": d, "muscle": tert, "volume": vol * (1/3)})
        
        df_m = pd.DataFrame(rows)
        if df_m.empty:
            st.info("No muscle metadata found in workouts. Make sure exercises.xlsx includes primary/secondary/tertiary muscles.")
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

# ===== Data Manager tab =====
with data_tab:
    st.subheader("📊 Data File Manager")
    st.markdown("Investigate, edit, and manage your workout data files.")
    
    # File selection
    data_source = st.selectbox(
        "Select data source",
        ["Strength Training Data", "Cardio Data", "Exercise Database"],
        help="Choose which data file to investigate and edit"
    )
    
    def load_data_for_editing(source_type):
        """Load data from local files or GitHub for editing"""
        if source_type == "Strength Training Data":
            if os.path.exists(WORKOUTS_CSV):
                return pd.read_csv(WORKOUTS_CSV), "workouts.csv", "strength"
            else:
                # Try GitHub
                try:
                    token, repo, branch, path_strength, path_cardio = github_env()
                    if token and repo and path_strength:
                        meta = github_get_file(token, repo, path_strength, ref=branch)
                        if meta:
                            existing_b64 = meta.get("content", "")
                            existing_bytes = base64.b64decode(existing_b64)
                            df = pd.read_csv(io.BytesIO(existing_bytes))
                            return df, "workouts.csv (from GitHub)", "strength"
                except Exception as e:
                    st.error(f"Error loading from GitHub: {e}")
                return pd.DataFrame(), "workouts.csv (not found)", "strength"
                
        elif source_type == "Cardio Data":
            if os.path.exists(CARDIO_CSV):
                return pd.read_csv(CARDIO_CSV), "cardio.csv", "cardio"
            else:
                # Try GitHub
                try:
                    token, repo, branch, path_strength, path_cardio = github_env()
                    if token and repo and path_cardio:
                        meta = github_get_file(token, repo, path_cardio, ref=branch)
                        if meta:
                            existing_b64 = meta.get("content", "")
                            existing_bytes = base64.b64decode(existing_b64)
                            df = pd.read_csv(io.BytesIO(existing_bytes))
                            return df, "cardio.csv (from GitHub)", "cardio"
                except Exception as e:
                    st.error(f"Error loading from GitHub: {e}")
                return pd.DataFrame(), "cardio.csv (not found)", "cardio"
                
        elif source_type == "Exercise Database":
            if os.path.exists(EXERCISES_XLSX):
                return pd.read_excel(EXERCISES_XLSX), "exercises.xlsx", "exercises"
            else:
                return pd.DataFrame(), "exercises.xlsx (not found)", "exercises"
    
    # Load selected data
    df_data, filename, data_type = load_data_for_editing(data_source)
    
    if df_data.empty:
        st.warning(f"No data found for {filename}")
        st.info("Try logging some workouts first, or check your file paths.")
    else:
        # Display file info
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("File", filename)
        with col2:
            st.metric("Rows", len(df_data))
        with col3:
            st.metric("Columns", len(df_data.columns))
        
        # Data exploration section
        st.subheader("🔍 Data Overview")
        
        # Show basic statistics
        if st.checkbox("Show data types and info"):
            st.markdown("**Data Types:**")
            info_data = []
            for col in df_data.columns:
                dtype = str(df_data[col].dtype)
                null_count = df_data[col].isnull().sum()
                info_data.append({
                    "Column": col,
                    "Data Type": dtype,
                    "Non-Null Count": len(df_data) - null_count,
                    "Null Count": null_count
                })
            st.dataframe(pd.DataFrame(info_data), use_container_width=True)
        
        # Date range filter for workout data
        if data_type in ["strength", "cardio"] and "workout_date" in df_data.columns:
            st.markdown("**📅 Date Range Filter**")
            df_data["workout_date"] = pd.to_datetime(df_data["workout_date"], errors="coerce")
            
            if not df_data["workout_date"].isna().all():
                min_date = df_data["workout_date"].min().date()
                max_date = df_data["workout_date"].max().date()
                
                col1, col2 = st.columns(2)
                with col1:
                    start_date = st.date_input("Start date", value=min_date, min_value=min_date, max_value=max_date)
                with col2:
                    end_date = st.date_input("End date", value=max_date, min_value=min_date, max_value=max_date)
                
                # Filter data by date range
                mask = (df_data["workout_date"].dt.date >= start_date) & (df_data["workout_date"].dt.date <= end_date)
                df_filtered = df_data[mask].copy()
            else:
                df_filtered = df_data.copy()
        else:
            df_filtered = df_data.copy()
        
        # Search and filter section
        st.subheader("🔎 Search & Filter")
        
        # Column-based filtering
        if len(df_filtered.columns) > 0:
            filter_column = st.selectbox("Filter by column", ["None"] + list(df_filtered.columns))
            
            if filter_column != "None":
                if df_filtered[filter_column].dtype == 'object':
                    # String column - show unique values
                    unique_values = df_filtered[filter_column].dropna().unique()
                    if len(unique_values) > 0:
                        selected_values = st.multiselect(
                            f"Select {filter_column} values",
                            options=sorted(unique_values),
                            default=[]
                        )
                        if selected_values:
                            df_filtered = df_filtered[df_filtered[filter_column].isin(selected_values)]
                else:
                    # Numeric column - show range slider
                    if not df_filtered[filter_column].isna().all():
                        min_val = float(df_filtered[filter_column].min())
                        max_val = float(df_filtered[filter_column].max())
                        if min_val != max_val:
                            range_values = st.slider(
                                f"{filter_column} range",
                                min_value=min_val,
                                max_value=max_val,
                                value=(min_val, max_val)
                            )
                            df_filtered = df_filtered[
                                (df_filtered[filter_column] >= range_values[0]) & 
                                (df_filtered[filter_column] <= range_values[1])
                            ]
        
        # Text search
        search_term = st.text_input("🔍 Search in all text columns", placeholder="Enter search term...")
        if search_term:
            text_columns = df_filtered.select_dtypes(include=['object']).columns
            mask = df_filtered[text_columns].astype(str).apply(
                lambda x: x.str.contains(search_term, case=False, na=False)
            ).any(axis=1)
            df_filtered = df_filtered[mask]
        
        # Display filtered results count
        st.info(f"Showing {len(df_filtered)} of {len(df_data)} total rows")
        
        # Special section for adding new exercises
        if data_type == "exercises":
            st.subheader("➕ Add New Exercise")
            
            with st.expander("Add a new exercise to the database", expanded=False):
                with st.form("add_exercise_form"):
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        new_exercise = st.text_input("Exercise Name*", placeholder="e.g., Bench Press")
                        new_variant = st.text_input("Variant", placeholder="e.g., Barbell, Dumbbell")
                        new_primary = st.text_input("Primary Muscle*", placeholder="e.g., Chest")
                    
                    with col2:
                        new_secondary = st.text_input("Secondary Muscle", placeholder="e.g., Triceps (optional)")
                        new_tertiary = st.text_input("Tertiary Muscle", placeholder="e.g., Front Delts (optional)")
                        
                        # Helpful muscle group suggestions
                        st.markdown("**Common muscle groups:**")
                        st.markdown("Chest, Back, Shoulders, Biceps, Triceps, Quads, Hamstrings, Glutes, Calves, Core, Lats, Traps")
                    
                    submitted = st.form_submit_button("➕ Add Exercise", type="primary")
                    
                    if submitted:
                        if not new_exercise.strip():
                            st.error("Exercise name is required!")
                        elif not new_primary.strip():
                            st.error("Primary muscle is required!")
                        else:
                            # Create new exercise row
                            new_row = {
                                "exercise": new_exercise.strip(),
                                "variant": new_variant.strip() if new_variant.strip() else "",
                                "primary_muscle": new_primary.strip(),
                                "secondary_muscle": new_secondary.strip() if new_secondary.strip() else "",
                                "tertiary_muscle": new_tertiary.strip() if new_tertiary.strip() else ""
                            }
                            
                            # Check for duplicates
                            duplicate_mask = (
                                (df_filtered["exercise"].str.lower() == new_exercise.strip().lower()) &
                                (df_filtered["variant"].str.lower() == new_variant.strip().lower())
                            )
                            
                            if duplicate_mask.any():
                                st.warning(f"⚠️ Exercise '{new_exercise}' with variant '{new_variant}' already exists!")
                                st.dataframe(df_filtered[duplicate_mask], use_container_width=True)
                            else:
                                # Add to dataframe
                                df_filtered = pd.concat([df_filtered, pd.DataFrame([new_row])], ignore_index=True)
                                st.success(f"✅ Added '{new_exercise}' to the exercise database!")
                                st.balloons()
            
            st.divider()
        
        # Data editing section
        st.subheader("✏️ Edit Data")
        
        # Data editor
        edited_df = st.data_editor(
            df_filtered,
            use_container_width=True,
            num_rows="dynamic",  # Allow adding/deleting rows
            key=f"data_editor_{data_type}"
        )
        
        # Save changes section
        st.subheader("💾 Save Changes")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # Save to local file
            if st.button("💾 Save to Local File", type="primary"):
                try:
                    if data_type == "exercises":
                        edited_df.to_excel(EXERCISES_XLSX, index=False)
                        st.success(f"✅ Saved to {EXERCISES_XLSX}")
                    elif data_type == "strength":
                        edited_df.to_csv(WORKOUTS_CSV, index=False)
                        st.success(f"✅ Saved to {WORKOUTS_CSV}")
                    elif data_type == "cardio":
                        edited_df.to_csv(CARDIO_CSV, index=False)
                        st.success(f"✅ Saved to {CARDIO_CSV}")
                except Exception as e:
                    st.error(f"❌ Error saving to local file: {e}")
        
        with col2:
            # Save to GitHub
            if st.button("☁️ Save to GitHub"):
                try:
                    if data_type == "strength":
                        ok, msg = save_csv_github_replace(edited_df, which="strength")
                        if ok:
                            st.success(f"✅ {msg}")
                        else:
                            st.error(f"❌ {msg}")
                    elif data_type == "cardio":
                        ok, msg = save_csv_github_replace(edited_df, which="cardio")
                        if ok:
                            st.success(f"✅ {msg}")
                        else:
                            st.error(f"❌ {msg}")
                    else:
                        st.warning("GitHub save not available for exercise database")
                except Exception as e:
                    st.error(f"❌ Error saving to GitHub: {e}")
        
        with col3:
            # Download as file
            if data_type == "exercises":
                # For Excel files, we need to create a BytesIO buffer
                from io import BytesIO
                buffer = BytesIO()
                edited_df.to_excel(buffer, index=False)
                buffer.seek(0)
                st.download_button(
                    "⬇️ Download Excel",
                    data=buffer.getvalue(),
                    file_name=f"edited_{filename}",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.download_button(
                    "⬇️ Download CSV",
                    data=edited_df.to_csv(index=False).encode("utf-8"),
                    file_name=f"edited_{filename}",
                    mime="text/csv"
                )
        
        # Data quality checks
        st.subheader("🔍 Data Quality Checks")
        
        # Check for missing values
        missing_data = edited_df.isnull().sum()
        if missing_data.sum() > 0:
            st.warning("⚠️ Missing values detected:")
            missing_df = pd.DataFrame({
                "Column": missing_data.index,
                "Missing Count": missing_data.values,
                "Missing %": (missing_data.values / len(edited_df) * 100).round(2)
            })
            missing_df = missing_df[missing_df["Missing Count"] > 0]
            st.dataframe(missing_df, use_container_width=True)
        else:
            st.success("✅ No missing values found")
        
        # Check for duplicates
        if data_type in ["strength", "cardio"]:
            key_columns = ["workout_date", "exercise"] if data_type == "strength" else ["workout_date", "activity"]
            if all(col in edited_df.columns for col in key_columns):
                duplicates = edited_df.duplicated(subset=key_columns, keep=False)
                if duplicates.any():
                    st.warning(f"⚠️ {duplicates.sum()} potential duplicate entries found")
                    if st.checkbox("Show duplicates"):
                        st.dataframe(edited_df[duplicates], use_container_width=True)
                else:
                    st.success("✅ No duplicates found")
        
        # Summary statistics for numeric columns
        numeric_columns = edited_df.select_dtypes(include=['number']).columns
        if len(numeric_columns) > 0:
            st.subheader("📈 Summary Statistics")
            st.dataframe(edited_df[numeric_columns].describe(), use_container_width=True)
