import streamlit as st
import pandas as pd
import google.generativeai as genai
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import calendar
import json
import altair as alt

# ------------------------------------------------------------------
# 1. CONFIGURATION & AUTH
# ------------------------------------------------------------------
st.set_page_config(page_title="Lumina AI", layout="wide", page_icon="💳")

# --- CUSTOM CSS FOR "PREMIUM" LOOK ---
# --- CUSTOM CSS FOR "PREMIUM" LIGHT MODE ---
def load_css():
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
        html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
        header {visibility: hidden;}
        
        /* Premium Light Background */
        .stApp { background: #f8fafc; color: #0f172a; }
        
        /* Light Mode Cards */
        .metric-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 12px; padding: 20px; text-align: center;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            transition: transform 0.2s;
        }
        .metric-card:hover { transform: translateY(-2px); border-color: #cbd5e1; }
        .metric-label { color: #64748b; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; font-weight: 600; }
        .metric-value { color: #0f172a; font-size: 2rem; font-weight: 700; }
        
        /* Chat Input and Tabs */
        .stChatInputContainer { border-radius: 20px !important; border: 1px solid #cbd5e1 !important; background: #ffffff !important; }
        .stTabs [data-baseweb="tab-list"] { gap: 10px; }
        .stTabs [data-baseweb="tab"] { background-color: #e2e8f0; border-radius: 8px; padding: 10px 20px; border: none; color: #475569; }
        .stTabs [aria-selected="true"] { background-color: #3b82f6 !important; color: white !important; }
        
        /* Text Colors */
        .stMarkdown p { color: #0f172a; }
        h1 { color: #0f172a !important; }
        </style>
    """, unsafe_allow_html=True)
load_css()

# Configure Gemini
try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash')
except Exception as e:
    st.error(f"Error configuring Gemini: {e}")
    st.stop()


# ------------------------------------------------------------------
# 2. DATA FUNCTIONS
# ------------------------------------------------------------------
def get_data():
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        # ttl=0 is the magic command. It forces a live, fresh pull from Google Sheets!
        df = conn.read(ttl=0) 
        required_cols = ["Date", "Item", "Amount", "Category", "Notes"]
        for col in required_cols:
            if col not in df.columns:
                df[col] = pd.Series(dtype='str')
        return df
    except Exception:
        return pd.DataFrame(columns=["Date", "Item", "Amount", "Category", "Notes"])

def save_expense(date, item, amount, category, notes):
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        existing_data = get_data()
        new_entry = pd.DataFrame([{
            "Date": date, "Item": item, "Amount": float(amount),
            "Category": category, "Notes": notes
        }])
        updated_df = pd.concat([existing_data, new_entry], ignore_index=True)
        conn.update(data=updated_df)
        
        # Nuke the memory so the dashboard updates instantly
        st.cache_data.clear() 
        return True
    except Exception:
        return False

# ------------------------------------------------------------------
# 3. AI LOGIC (THE BRAIN)
# ------------------------------------------------------------------
def analyze_intent_and_process(user_input, current_df):
    # Separate budget rules from actual expenses
    current_budget = 0
    monthly_spent = 0
    data_summary = ""
    
    current_date = datetime.now()
    days_in_month = calendar.monthrange(current_date.year, current_date.month)[1]
    days_left = days_in_month - current_date.day

    if not current_df.empty:
        # Find the latest set budget
        budget_rows = current_df[current_df["Category"] == "SYSTEM_BUDGET"]
        if not budget_rows.empty:
            current_budget = float(budget_rows.iloc[-1]["Amount"])
            
        # Calculate spent this month
        current_df["Date"] = pd.to_datetime(current_df["Date"], errors='coerce')
        expenses_df = current_df[current_df["Category"] != "SYSTEM_BUDGET"]
        mask = (expenses_df["Date"].dt.month == current_date.month) & (expenses_df["Date"].dt.year == current_date.year)
        monthly_spent = pd.to_numeric(expenses_df.loc[mask, "Amount"], errors='coerce').sum()
        
        data_summary = expenses_df.tail(20).to_csv(index=False)
    
    system_prompt = f"""
    You are Lumina, an elite, highly intelligent Financial Advisor AI for an Indian user.
    Current Date: {current_date.strftime("%Y-%m-%d")}. Days left in month: {days_left}.
    
    USER CONTEXT:
    - Monthly Budget: ₹{current_budget if current_budget > 0 else 'Not set yet'}
    - Total Spent This Month: ₹{monthly_spent}
    - Recent Data: {data_summary}

    Classify the user's input into one of THREE INTENTS:
    
    1. INTENT: LOG_EXPENSE
    - Extract Details: date (YYYY-MM-DD), item, amount (number only), category, notes.
    - GENERATE 'response_text': Acknowledge the expense. THEN, act as a financial coach. 
      * Calculate if this expense pushes them over their budget or burns through it too fast given the {days_left} days left.
      * Give a proactive warning or a smart observation based on the category.
      * Be conversational, premium, and use emojis.

    2. INTENT: SET_BUDGET
    - The user wants to set a monthly limit (e.g., "set budget to 35000").
    - Extract the amount.
    - GENERATE 'response_text': Enthusiastically confirm the new budget rule and give a quick tip on daily spending limits.
    - Output JSON: {{ "intent": "SET_BUDGET", "amount": float, "response_text": "string" }}

    3. INTENT: QUERY
    - Answer financial questions based on the Data. Act like a top-tier analyst finding insights (e.g. "You spend the most on X").
    - Output JSON: {{ "intent": "QUERY", "response_text": "string" }}

    USER INPUT: "{user_input}"

    Respond ONLY with the JSON object. 
    """
    
    # --- THIS IS THE CRITICAL PART THAT WAS MISSING ---
    try:
        response = model.generate_content(system_prompt)
        text_response = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(text_response)
    except Exception as e:
        return {"intent": "ERROR", "response_text": f"I encountered an error analyzing that. Can we try again?"}

# ------------------------------------------------------------------
# 4. UI & STATE MANAGEMENT
# ------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "Hello! I am Lumina. Try saying **'Set my monthly budget to 35000'**, or log an expense like **'Spent 200 on auto'** to see my analysis!"}]

st.markdown("<h1 style='text-align: left; color: #fff;'>💳 Lumina AI Expense Manager</h1>", unsafe_allow_html=True)

tab1, tab2 = st.tabs(["💬 Assistant", "📊 Dashboard"])

# --- TAB 1: CHAT ---
with tab1:
    with st.container():
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    if prompt := st.chat_input("Log an expense, ask a question, or set a budget..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.spinner("Analyzing your finances..."):
            current_df = get_data()
            ai_result = analyze_intent_and_process(prompt, current_df)

        response_msg = ai_result.get("response_text", "Done!")

        if ai_result.get("intent") == "SET_BUDGET":
            # Save budget as a special system row
            save_expense(datetime.now().strftime("%Y-%m-%d"), "MONTHLY_BUDGET", ai_result["amount"], "SYSTEM_BUDGET", "")
            
        elif ai_result.get("intent") == "LOG_EXPENSE":
            save_expense(
                ai_result.get('date'), 
                ai_result.get('item', 'Expense'), 
                ai_result.get('amount'), 
                ai_result.get('category', 'Other'), 
                ""
            )

        with st.chat_message("assistant"):
            st.markdown(response_msg)
        st.session_state.messages.append({"role": "assistant", "content": response_msg})

# --- TAB 2: DASHBOARD ---
with tab2:
    raw_df = get_data()
    
    if not raw_df.empty:
        raw_df["Amount"] = pd.to_numeric(raw_df["Amount"], errors='coerce').fillna(0.0)
        
        # Extract Budget
        budget_rows = raw_df[raw_df["Category"] == "SYSTEM_BUDGET"]
        monthly_budget = budget_rows["Amount"].iloc[-1] if not budget_rows.empty else 0
        
        # Clean Expenses
        df = raw_df[raw_df["Category"] != "SYSTEM_BUDGET"].copy()
        df["Date"] = pd.to_datetime(df["Date"], errors='coerce')
        
        current_month = datetime.now().month
        current_year = datetime.now().year
        monthly_mask = (df["Date"].dt.month == current_month) & (df["Date"].dt.year == current_year)
        monthly_spent = df.loc[monthly_mask, "Amount"].sum()

        # Visual Budget Tracker
        if monthly_budget > 0:
            st.markdown(f"### 🎯 Monthly Budget: ₹{monthly_budget:,.0f}")
            progress_pct = min(monthly_spent / monthly_budget, 1.0)
            
            # Change color based on burn rate
            if progress_pct > 0.9: st.error(f"You've spent {progress_pct*100:.1f}% of your budget!")
            elif progress_pct > 0.7: st.warning(f"You've spent {progress_pct*100:.1f}% of your budget.")
            else: st.success(f"You've spent {progress_pct*100:.1f}% of your budget. Looking good!")
            
            st.progress(progress_pct)
            st.markdown("---")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">Spent This Month</div>
                <div class="metric-value">₹{monthly_spent:,.0f}</div>
            </div>
            """, unsafe_allow_html=True)
            
        with col2:
            remaining = (monthly_budget - monthly_spent) if monthly_budget > 0 else 0
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">Remaining Budget</div>
                <div class="metric-value">₹{remaining:,.0f}</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # --- CHART ---
        cat_group = df.groupby("Category")["Amount"].sum().reset_index()
        if not cat_group.empty and cat_group["Amount"].sum() > 0:
            chart = alt.Chart(cat_group).mark_bar(cornerRadiusTopRight=10, cornerRadiusBottomRight=10).encode(
                x=alt.X('Amount', title='Total Spent (₹)'),
                y=alt.Y('Category', sort='-x', title=''),
                color=alt.Color('Amount', scale=alt.Scale(scheme='blues'), legend=None),
                tooltip=['Category', 'Amount']
            ).properties(height=350).configure_axis(labelColor='#475569', titleColor='#0f172a', grid=False).configure_view(strokeWidth=0)
            st.altair_chart(chart, use_container_width=True)

        # --- RECENT TRANSACTIONS ---
        st.markdown("### 🕒 Recent Transactions")
        display_df = df[["Date", "Item", "Category", "Amount"]].sort_values(by="Date", ascending=False).head(5)
        display_df["Date"] = display_df["Date"].dt.strftime('%Y-%m-%d')
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        
    else:
        st.info("Start chatting to add your first expense or set a budget!")
