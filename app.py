import streamlit as st
import pandas as pd
import google.generativeai as genai
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import json
import altair as alt

# ------------------------------------------------------------------
# 1. CONFIGURATION & AUTH
# ------------------------------------------------------------------
st.set_page_config(page_title="AI Expense Manager", layout="wide", page_icon="💳")

# --- CUSTOM CSS FOR "PREMIUM" LOOK ---
def load_css():
    st.markdown("""
        <style>
        /* Import Google Font */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
        }

        /* Hides top header line */
        header {visibility: hidden;}
        
        /* Modern Dark Gradient Background */
        .stApp {
            background: linear-gradient(to bottom right, #0e1117, #1a1c24);
        }

        /* Custom Card Style for Metrics */
        .metric-card {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
            text-align: center;
            transition: transform 0.2s;
        }
        .metric-card:hover {
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.3);
        }
        .metric-label {
            color: #a0a0a0;
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 5px;
        }
        .metric-value {
            color: #ffffff;
            font-size: 2rem;
            font-weight: 700;
            background: -webkit-linear-gradient(45deg, #4facfe, #00f2fe);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        /* Styled Chat Input */
        .stChatInputContainer {
            border-radius: 20px !important;
            border: 1px solid #333 !important;
        }
        
        /* Custom Tab Styling */
        .stTabs [data-baseweb="tab-list"] {
            gap: 10px;
        }
        .stTabs [data-baseweb="tab"] {
            background-color: rgba(255,255,255,0.05);
            border-radius: 8px;
            padding: 10px 20px;
            border: none;
            color: #fff;
        }
        .stTabs [aria-selected="true"] {
            background-color: #4facfe !important;
            color: white !important;
        }

        /* DataFrame Styling */
        [data-testid="stDataFrame"] {
            background: rgba(255, 255, 255, 0.02);
            border-radius: 10px;
            padding: 10px;
        }
        </style>
    """, unsafe_allow_html=True)

load_css()

# Check Password
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.markdown("<h1 style='text-align: center;'>🔒 Login</h1>", unsafe_allow_html=True)
        password = st.text_input("Enter Password", type="password")
        if st.button("Unlock", use_container_width=True):
            if password == st.secrets["APP_PASSWORD"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password")
    st.stop()

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
    """Fetch data from Google Sheets."""
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df = conn.read()
        required_cols = ["Date", "Item", "Amount", "Category", "Notes"]
        for col in required_cols:
            if col not in df.columns:
                df[col] = pd.Series(dtype='str')
        return df
    except Exception as e:
        st.error(f"Error connecting to Google Sheets: {e}")
        return pd.DataFrame(columns=["Date", "Item", "Amount", "Category", "Notes"])

def save_expense(date, item, amount, category, notes):
    """Append a new expense."""
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        existing_data = get_data()
        new_entry = pd.DataFrame([{
            "Date": date, "Item": item, "Amount": float(amount),
            "Category": category, "Notes": notes
        }])
        updated_df = pd.concat([existing_data, new_entry], ignore_index=True)
        conn.update(data=updated_df)
        st.toast(f"✅ Saved: {item} - ₹{amount} ({category})")
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Failed to save data: {e}")
        return False

# ------------------------------------------------------------------
# 3. AI LOGIC
# ------------------------------------------------------------------
def analyze_intent_and_process(user_input, current_df):
    data_summary = ""
    if not current_df.empty:
        data_summary = current_df.to_csv(index=False)

    current_date = datetime.now().strftime("%Y-%m-%d")
    
    system_prompt = f"""
    You are an intelligent Expense Manager AI for an Indian user. Current Date: {current_date}.
    
    Your goal is to classify the user's input into one of two INTENTS: "LOG_EXPENSE" or "QUERY".
    
    1. INTENT: LOG_EXPENSE
    If the user describes spending money, extract the details.
    - Parse relative dates (e.g., "yesterday", "last friday") into YYYY-MM-DD. Default to {current_date} if not specified.
    - Extract Item, Amount (number only), Category, and Notes.
    - **AUTO-CATEGORIZATION RULES**:
      ['Food', 'Groceries', 'Utility Bills', 'Travel', 'Shopping', 'Entertainment', 'Health', 'Education', 'Other']
      *Examples:* "Starbucks"->"Food", "Uber"->"Travel", "Blinkit"->"Groceries".
    - Only set "Category" to "UNCERTAIN" if completely ambiguous.
    - Output JSON: {{ "intent": "LOG_EXPENSE", "date": "YYYY-MM-DD", "item": "string", "amount": float, "category": "string", "notes": "string" }}

    2. INTENT: QUERY
    If the user asks a question about spending, calculate the answer from data.
    - Data Context: {data_summary}
    - Output JSON: {{ "intent": "QUERY", "response_text": "Natural language answer." }}

    USER INPUT: "{user_input}"
    Respond ONLY with the JSON object.
    """
    
    response = model.generate_content(system_prompt)
    text_response = response.text.strip().replace("```json", "").replace("```", "")
    try:
        return json.loads(text_response)
    except Exception as e:
        return {"intent": "ERROR", "response_text": f"Error: {text_response}"}

# ------------------------------------------------------------------
# 4. UI & STATE MANAGEMENT
# ------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "Hello! I can track your expenses. Try 'Spent 200 rupees on auto' or 'How much did I spend on Food?'"}]
if "pending_expense" not in st.session_state:
    st.session_state.pending_expense = None 

# Styled Header
st.markdown("<h1 style='text-align: left; color: #fff;'>💳 AI Expense Manager</h1>", unsafe_allow_html=True)

tab1, tab2 = st.tabs(["💬 Chat Assistant", "📊 Dashboard"])

# --- TAB 1: CHAT ---
with tab1:
    # Chat container
    with st.container():
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    if prompt := st.chat_input("Type your expense or question here..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Pending Category Logic
        if st.session_state.pending_expense:
            category_input = prompt.strip().title()
            pending = st.session_state.pending_expense
            success = save_expense(pending['date'], pending['item'], pending['amount'], category_input, pending['notes'])
            
            if success:
                response_msg = f"Got it! Categorized as **{category_input}** and saved."
                st.session_state.pending_expense = None
            else:
                response_msg = "Something went wrong saving the data."
            
            with st.chat_message("assistant"):
                st.markdown(response_msg)
            st.session_state.messages.append({"role": "assistant", "content": response_msg})

        else:
            with st.spinner("Analyzing..."):
                current_df = get_data()
                ai_result = analyze_intent_and_process(prompt, current_df)

            if ai_result.get("intent") == "QUERY":
                response_msg = ai_result.get("response_text")
                with st.chat_message("assistant"):
                    st.markdown(response_msg)
                st.session_state.messages.append({"role": "assistant", "content": response_msg})

            elif ai_result.get("intent") == "LOG_EXPENSE":
                data = ai_result
                if data.get("category") == "UNCERTAIN":
                    st.session_state.pending_expense = data
                    response_msg = f"I noticed you spent **₹{data['amount']}** on **{data['item']}**, but I'm not sure about the category. Which one is it?"
                    with st.chat_message("assistant"):
                        st.markdown(response_msg)
                    st.session_state.messages.append({"role": "assistant", "content": response_msg})
                else:
                    success = save_expense(data['date'], data['item'], data['amount'], data['category'], data['notes'])
                    if success:
                        response_msg = f"✅ Saved: **₹{data['amount']}** for **{data['item']}** ({data['category']})."
                    else:
                        response_msg = "Failed to save."
                    with st.chat_message("assistant"):
                        st.markdown(response_msg)
                    st.session_state.messages.append({"role": "assistant", "content": response_msg})
            else:
                response_msg = "I'm sorry, I didn't understand that. Please try again."
                with st.chat_message("assistant"):
                    st.markdown(response_msg)
                st.session_state.messages.append({"role": "assistant", "content": response_msg})

# --- TAB 2: DASHBOARD ---
with tab2:
    df = get_data()
    
    if not df.empty:
        # Cleanup
        df["Amount"] = pd.to_numeric(df["Amount"], errors='coerce').fillna(0.0)
        df["Category"] = df["Category"].fillna("Uncategorized").astype(str)
        df["Date"] = pd.to_datetime(df["Date"], errors='coerce')

        # --- CUSTOM METRIC CARDS ---
        total_spent = df["Amount"].sum()
        
        # Calculate Monthly
        current_month = datetime.now().month
        current_year = datetime.now().year
        monthly_mask = (df["Date"].dt.month == current_month) & (df["Date"].dt.year == current_year)
        monthly_spent = df.loc[monthly_mask, "Amount"].sum()

        col1, col2 = st.columns(2)
        
        # Inject HTML for Cards
        with col1:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">Total Spent (All Time)</div>
                <div class="metric-value">₹{total_spent:,.0f}</div>
            </div>
            """, unsafe_allow_html=True)
            
        with col2:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">This Month</div>
                <div class="metric-value">₹{monthly_spent:,.0f}</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")

        # --- CHART ---
        st.subheader("📊 Expenses by Category")
        
        try:
            cat_group = df.groupby("Category")["Amount"].sum().reset_index()
            if not cat_group.empty and cat_group["Amount"].sum() > 0:
                # Premium Altair Chart
                chart = alt.Chart(cat_group).mark_bar(cornerRadiusTopRight=10, cornerRadiusBottomRight=10).encode(
                    x=alt.X('Amount', title='Total Spent (₹)'),
                    y=alt.Y('Category', sort='-x', title='Category'),
                    color=alt.Color('Amount', scale=alt.Scale(scheme='blues'), legend=None),
                    tooltip=['Category', 'Amount']
                ).properties(height=350).configure_axis(
                    labelColor='#ddd', titleColor='#aaa', grid=False
                ).configure_view(strokeWidth=0)
                
                st.altair_chart(chart, use_container_width=True)
            else:
                st.info("Add some expenses to see your breakdown!")
        except Exception as e:
            st.warning(f"Chart Error: {e}")

        # --- RECENT TRANSACTIONS ---
        st.markdown("### 🕒 Recent Transactions")
        st.dataframe(
            df.sort_values(by="Date", ascending=False).head(5), 
            use_container_width=True,
            hide_index=True
        )
        
    else:
        st.info("Start chatting to add your first expense!")
