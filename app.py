import streamlit as st
import pandas as pd
import datetime
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from openai import OpenAI
import os

# --- Configuration & Setup ---
st.set_page_config(page_title="Skylark Drones AI Coordinator", layout="wide")
st.title("🚁 Skylark Drones Operations AI Agent")

# Initialize OpenAI Client (Make sure to add OPENAI_API_KEY in Streamlit Secrets)
api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
client = OpenAI(api_key=api_key) if api_key else None

# --- Data Loading & Google Sheets Sync ---
# For this prototype, we simulate Google Sheets sync using session state initialized by the CSVs.
# In production, this uses gspread to read/write directly to the Google Sheet URLs.

@st.cache_data
def load_data():
    pilots = pd.read_csv("pilot_roster.csv")
    drones = pd.read_csv("drone_fleet.csv")
    missions = pd.read_csv("missions.csv")
    return pilots, drones, missions

if 'pilots' not in st.session_state:
    st.session_state.pilots, st.session_state.drones, st.session_state.missions = load_data()

# Example Google Sheets Sync Function (Mocked for safety without credentials)
def sync_to_google_sheets(df, sheet_name):
    """
    Actual Implementation requires a service_account.json:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
    client = gspread.authorize(creds)
    sheet = client.open('Skylark_Database').worksheet(sheet_name)
    sheet.update([df.columns.values.tolist()] + df.values.tolist())
    """
    # st.success(f"Successfully synced {sheet_name} to Google Sheets!")
    pass

# --- Core Agent Functions (Tools for LLM) ---

def check_conflicts(pilot_id, drone_id, project_id):
    """Conflict Detection Engine"""
    pilot = st.session_state.pilots[st.session_state.pilots['pilot_id'] == pilot_id].iloc[0]
    drone = st.session_state.drones[st.session_state.drones['drone_id'] == drone_id].iloc[0]
    mission = st.session_state.missions[st.session_state.missions['project_id'] == project_id].iloc[0]
    
    conflicts = []
    
    # 1. Date / Double Booking (Simplified logic)
    if pilot['status'] != 'Available':
         conflicts.append(f"Pilot {pilot['name']} is currently {pilot['status']}.")
    
    # 2. Skill & Cert Mismatch
    mission_skills = [s.strip() for s in mission['required_skills'].split(',')]
    pilot_skills = [s.strip() for s in pilot['skills'].split(',')]
    if not all(s in pilot_skills for s in mission_skills):
        conflicts.append(f"Skill mismatch: Mission requires {mission['required_skills']}.")
        
    mission_certs = [c.strip() for c in str(mission['required_certs']).split(',')]
    pilot_certs = [c.strip() for c in str(pilot['certifications']).split(',')]
    if not all(c in pilot_certs for c in mission_certs):
        conflicts.append(f"Cert mismatch: Mission requires {mission['required_certs']}.")
        
    # 3. Location Mismatch
    if pilot['location'] != mission['location']:
        conflicts.append(f"Location mismatch: Pilot in {pilot['location']}, Mission in {mission['location']}.")
        
    # 4. Budget Overrun
    start = datetime.datetime.strptime(mission['start_date'], '%Y-%m-%d')
    end = datetime.datetime.strptime(mission['end_date'], '%Y-%m-%d')
    days = (end - start).days + 1
    total_cost = days * pilot['daily_rate_inr']
    if total_cost > mission['mission_budget_inr']:
        conflicts.append(f"Budget Overrun: Pilot cost ₹{total_cost} exceeds budget ₹{mission['mission_budget_inr']}.")
        
    # 5. Weather Risk
    if mission['weather_forecast'].lower() == 'rainy' and 'rain' not in str(drone['weather_resistance']).lower():
        conflicts.append(f"Weather Risk: Drone {drone['model']} is not rated for Rainy conditions.")
        
    # 6. Maintenance Check
    maint_date = datetime.datetime.strptime(drone['maintenance_due'], '%Y-%m-%d')
    if maint_date <= start:
        conflicts.append(f"Maintenance Due: Drone {drone['model']} requires maintenance before mission start.")
        
    return conflicts

def handle_urgent_reassignment(project_id):
    """Urgent Reassignment Logic"""
    mission = st.session_state.missions[st.session_state.missions['project_id'] == project_id].iloc[0]
    if mission['priority'].lower() != 'urgent':
        return json.dumps({"status": "Failed", "reason": "Mission is not Urgent."})
        
    # Find active Standard missions to preempt
    standard_missions = st.session_state.missions[st.session_state.missions['priority'] == 'Standard']
    preemptable_pilots = st.session_state.pilots[
        (st.session_state.pilots['status'] == 'Assigned') & 
        (st.session_state.pilots['current_assignment'].isin(standard_missions['project_id'].tolist()))
    ]
    
    if preemptable_pilots.empty:
        return json.dumps({"status": "Failed", "reason": "No preemptable standard assignments found."})
        
    suggested_pilot = preemptable_pilots.iloc[0]
    return json.dumps({
        "status": "Success", 
        "action_recommended": f"Preempt Pilot {suggested_pilot['name']} from {suggested_pilot['current_assignment']} and reassign to {project_id}."
    })

def update_pilot_status(pilot_id, new_status):
    idx = st.session_state.pilots.index[st.session_state.pilots['pilot_id'] == pilot_id].tolist()
    if idx:
        st.session_state.pilots.at[idx[0], 'status'] = new_status
        sync_to_google_sheets(st.session_state.pilots, "Pilot_Roster")
        return json.dumps({"status": "Success", "message": f"Updated pilot {pilot_id} status to {new_status}."})
    return json.dumps({"status": "Error", "message": "Pilot not found."})

# --- Conversational UI Setup ---

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "Hello! I am the Skylark Drones Operations Coordinator AI. How can I assist you with the roster, fleet, or mission assignments today?"}]

for msg in st.session_state.messages:
    st.chat_message(msg["role"]).write(msg["content"])

user_input = st.chat_input("Ask about pilot availability, assign missions, or check conflicts...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    st.chat_message("user").write(user_input)
    
    if not client:
         st.error("Please configure your OPENAI_API_KEY to use the conversational agent.")
    else:
        # Define Tools for OpenAI
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "check_conflicts",
                    "description": "Checks for any conflicts (budget, weather, skills, dates) before assigning a pilot and drone to a mission.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pilot_id": {"type": "string"},
                            "drone_id": {"type": "string"},
                            "project_id": {"type": "string"}
                        },
                        "required": ["pilot_id", "drone_id", "project_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "handle_urgent_reassignment",
                    "description": "Attempts to find a pilot from a lower priority mission to reassign to an Urgent mission.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string", "description": "The ID of the urgent project"}
                        },
                        "required": ["project_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_pilot_status",
                    "description": "Updates the status of a pilot (e.g., 'Available', 'On Leave', 'Assigned') and syncs it to Google Sheets.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pilot_id": {"type": "string"},
                            "new_status": {"type": "string"}
                        },
                        "required": ["pilot_id", "new_status"]
                    }
                }
            }
        ]

        # Context injection (Inject current state into system prompt)
        system_prompt = f"""You are the Skylark Drones Operations AI. You help manage pilots, drones, and missions.
        Current Pilot Roster: {st.session_state.pilots.to_dict(orient='records')}
        Current Drone Fleet: {st.session_state.drones.to_dict(orient='records')}
        Current Missions: {st.session_state.missions.to_dict(orient='records')}
        Analyze the data provided and answer queries. Use tools when you need to perform actions or complex conflict checks."""

        messages_for_api = [{"role": "system", "content": system_prompt}] + st.session_state.messages

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages_for_api,
            tools=tools,
            tool_choice="auto"
        )

        response_message = response.choices[0].message
        
        # Handle Tool Calls
        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                args = json.loads(tool_call.function.arguments)
                if tool_call.function.name == "check_conflicts":
                    res = check_conflicts(args["pilot_id"], args["drone_id"], args["project_id"])
                    st.session_state.messages.append({"role": "assistant", "content": f"Conflict Check Results: {res if res else 'No conflicts detected! Safe to assign.'}"})
                elif tool_call.function.name == "handle_urgent_reassignment":
                    res = handle_urgent_reassignment(args["project_id"])
                    st.session_state.messages.append({"role": "assistant", "content": f"Reassignment Engine: {res}"})
                elif tool_call.function.name == "update_pilot_status":
                    res = update_pilot_status(args["pilot_id"], args["new_status"])
                    st.session_state.messages.append({"role": "assistant", "content": f"Status Update: {res}"})
        else:
            st.session_state.messages.append({"role": "assistant", "content": response_message.content})
        
        st.rerun()