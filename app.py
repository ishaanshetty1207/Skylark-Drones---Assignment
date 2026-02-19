import streamlit as st
import pandas as pd
import datetime
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from openai import OpenAI
import json

# ---------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------
st.set_page_config(page_title="Skylark Drones AI Coordinator", layout="wide")
st.title("🚁 Skylark Drones Operations AI Agent")

# ---------------------------------------------------
# OPENAI SETUP
# ---------------------------------------------------
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None

# ---------------------------------------------------
# GOOGLE SHEETS CONNECTION
# ---------------------------------------------------
@st.cache_resource
def connect_to_sheets():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS"))

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, scope
    )
    return gspread.authorize(creds)

gc = connect_to_sheets()
SPREADSHEET_NAME = "Skylark Drone Database"

def load_data():
    sheet = gc.open(SPREADSHEET_NAME)
    pilots = pd.DataFrame(sheet.worksheet("pilot_roster").get_all_records())
    drones = pd.DataFrame(sheet.worksheet("drone_fleet").get_all_records())
    missions = pd.DataFrame(sheet.worksheet("missions").get_all_records())
    return pilots, drones, missions

if "pilots" not in st.session_state:
    st.session_state.pilots, st.session_state.drones, st.session_state.missions = load_data()

# ---------------------------------------------------
# SYNC FUNCTION
# ---------------------------------------------------
def sync_sheet(df, sheet_name):
    sheet = gc.open(SPREADSHEET_NAME).worksheet(sheet_name)
    sheet.clear()
    sheet.update([df.columns.values.tolist()] + df.values.tolist())

# ---------------------------------------------------
# BUSINESS LOGIC FUNCTIONS
# ---------------------------------------------------
def check_conflicts(pilot_id, drone_id, project_id):

    pilots = st.session_state.pilots
    drones = st.session_state.drones
    missions = st.session_state.missions

    try:
        pilot = pilots[pilots["pilot_id"] == pilot_id].iloc[0]
        drone = drones[drones["drone_id"] == drone_id].iloc[0]
        mission = missions[missions["project_id"] == project_id].iloc[0]
    except:
        return ["Invalid IDs provided."]

    conflicts = []

    if pilot["status"] != "Available":
        conflicts.append(f"Pilot {pilot['name']} is currently {pilot['status']}.")

    required_skills = [s.strip() for s in mission["required_skills"].split(",")]
    pilot_skills = [s.strip() for s in pilot["skills"].split(",")]

    if not all(skill in pilot_skills for skill in required_skills):
        conflicts.append("Skill mismatch detected.")

    required_certs = str(mission["required_certs"]).split(",")
    pilot_certs = str(pilot["certifications"]).split(",")

    if not all(cert.strip() in pilot_certs for cert in required_certs):
        conflicts.append("Certification mismatch detected.")

    if pilot["location"] != mission["location"]:
        conflicts.append("Location mismatch.")

    start = datetime.datetime.strptime(mission["start_date"], "%Y-%m-%d")
    end = datetime.datetime.strptime(mission["end_date"], "%Y-%m-%d")
    days = (end - start).days + 1
    total_cost = days * float(pilot["daily_rate_inr"])

    if total_cost > float(mission["mission_budget_inr"]):
        conflicts.append(f"Budget overrun: ₹{total_cost}")

    if mission["weather_forecast"].lower() == "rainy":
        if "rain" not in str(drone["weather_resistance"]).lower():
            conflicts.append("Drone not rated for rainy weather.")

    maint = datetime.datetime.strptime(drone["maintenance_due"], "%Y-%m-%d")
    if maint <= start:
        conflicts.append("Drone maintenance due before mission.")

    return conflicts


def handle_urgent_reassignment(project_id):

    missions = st.session_state.missions
    pilots = st.session_state.pilots

    try:
        mission = missions[missions["project_id"] == project_id].iloc[0]
    except:
        return "Invalid project ID."

    if mission["priority"].lower() != "urgent":
        return "Mission is not marked as Urgent."

    assigned = pilots[pilots["status"] == "Assigned"]

    if assigned.empty:
        return "No pilots available for reassignment."

    suggested = assigned.iloc[0]

    return f"Suggested: Reassign Pilot {suggested['name']} from {suggested['current_assignment']} to {project_id}"


def update_pilot_status(pilot_id, new_status):

    idx = st.session_state.pilots.index[
        st.session_state.pilots["pilot_id"] == pilot_id
    ].tolist()

    if idx:
        st.session_state.pilots.at[idx[0], "status"] = new_status
        sync_sheet(st.session_state.pilots, "pilot_roster")
        return f"Pilot {pilot_id} updated to {new_status}"

    return "Pilot not found."


# ---------------------------------------------------
# CHAT UI
# ---------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Hello! I manage pilots, drones and missions. How can I help?"}
    ]

for msg in st.session_state.messages:
    st.chat_message(msg["role"]).write(msg["content"])

user_input = st.chat_input("Ask something...")

if user_input:

    st.session_state.messages.append({"role": "user", "content": user_input})
    st.chat_message("user").write(user_input)

    # ---------- RULE-BASED ROUTING (NO LARGE TOKEN USE) ----------

    text = user_input.lower()

    if "conflict" in text:
        words = user_input.split()
        if len(words) >= 7:
            pilot_id = words[4]
            drone_id = words[6]
            project_id = words[8]
            result = check_conflicts(pilot_id, drone_id, project_id)
            response = result if result else ["No conflicts detected. Safe to assign."]
        else:
            response = ["Please provide pilot_id, drone_id, and project_id."]

    elif "urgent" in text:
        words = user_input.split()
        project_id = words[-1]
        response = handle_urgent_reassignment(project_id)

    elif "update" in text:
        words = user_input.split()
        pilot_id = words[2]
        new_status = words[-1]
        response = update_pilot_status(pilot_id, new_status)

    else:
        response = "Please ask about conflicts, urgent reassignment, or status updates."

    st.session_state.messages.append({"role": "assistant", "content": str(response)})
    st.rerun()