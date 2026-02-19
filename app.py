import streamlit as st
import pandas as pd
import google.generativeai as genai
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import json
import re

# ------------------------------------------------------------------
# 1. CONFIGURATION & AUTH
# ------------------------------------------------------------------
st.set_page_config(page_title="AI Expense Manager", layout="wide")

# Check Password
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 Login Required")
    password = st.text_input("Enter Password", type="password")
    if st.button("Login"):
        if password == st.secrets["APP_PASSWORD"]:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

# Configure Gemini
try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    # Switched back to 2.0 Flash since 1.5 is unavailable for your key
    model = genai.GenerativeModel('gemini-2.0-flash')
except Exception as e:
    st.error(f"Error configuring Gemini: {e}")
    st.stop()

# ------------------------------------------------------------------
# 2. DATA FUNCTIONS (Google Sheets)
# ------------------------------------------------------------------
def get_data():
    """Fetch data from Google Sheets."""
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df = conn.read()
        # Ensure required columns exist
        required_cols = ["Date", "Item", "Amount", "Category", "Notes"]
        for col in required_cols:
            if col not in df.columns:
                df[col] = pd.Series(dtype='str')
        return df
    except Exception as e:
        st.error(f"Error connecting to Google Sheets: {e}")
        return pd.DataFrame(columns=["Date", "Item", "Amount", "Category", "Notes"])

def save_expense(date, item, amount, category, notes):
    """Append a new expense to Google Sheets."""
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        existing_data = get_data()
        
        new_entry = pd.DataFrame([{
            "Date": date,
            "Item": item,
            "Amount": float(amount),
            "Category": category,
            "Notes": notes
        }])
        
        updated_df = pd.concat([existing_data, new_entry], ignore_index=True)
        conn.update(data=updated_df)
        st.toast(f"✅ Saved: {item} - ₹{amount} ({category})")
        # Clear cache to reflect updates immediately
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Failed to save data: {e}")
        return False

# ------------------------------------------------------------------
# 3. AI LOGIC
# ------------------------------------------------------------------
def analyze_intent_and_process(user_input, current_df):
    """
    Determines if input is a query or an entry. 
    Returns a JSON object with intent and data.
    """
    
    # Context data for the AI to answer queries
    data_summary = ""
    if not current_df.empty:
        # Pass a summarized version to save tokens
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
      Map the item/context to one of these standardized categories:
      ['Food', 'Groceries', 'Utility Bills', 'Travel', 'Shopping', 'Entertainment', 'Health', 'Education', 'Other']
      
      *Examples:*
      - "Starbucks", "Lunch", "Burger", "Zomato" -> "Food"
      - "Uber", "Ola", "Bus ticket", "Petrol", "Auto" -> "Travel"
      - "Electricity", "Internet", "Recharge", "Bescom" -> "Utility Bills"
      - "Medicine", "Doctor", "Pharmacy" -> "Health"
      - "Vegetables", "Milk", "Zepto", "Blinkit" -> "Groceries"
      
    - Only set "Category" to "UNCERTAIN" if the item is completely ambiguous (e.g., "Paid 500 to Rahul").
    
    - Output JSON format: 
      {{ "intent": "LOG_EXPENSE", "date": "YYYY-MM-DD", "item": "string", "amount": float, "category": "string", "notes": "string" }}

    2. INTENT: QUERY
    If the user asks a question about their spending history (e.g., "How much did I spend on food?"), use the provided CSV data context to calculate the answer.
    - Data Context: 
    {data_summary}
    - Output JSON format:
      {{ "intent": "QUERY", "response_text": "Your natural language answer here based on the data." }}

    USER INPUT: "{user_input}"
    
    Respond ONLY with the JSON object. Do not add markdown formatting like ```json.
    """
    
    response = model.generate_content(system_prompt)
    text_response = response.text.strip()
    
    # Cleanup json markdown if present
    if text_response.startswith("```json"):
        text_response = text_response.replace("```json", "").replace("```", "")
    
    try:
        return json.loads(text_response)
    except Exception as e:
        return {"intent": "ERROR", "response_text": f"Raw AI response error: {text_response}"}

# ------------------------------------------------------------------
# 4. UI & STATE MANAGEMENT
# ------------------------------------------------------------------

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "Hello! I can track your expenses. Try 'Spent 200 rupees on auto today' or 'Paid 500 for lunch'."}]
if "pending_expense" not in st.session_state:
    st.session_state.pending_expense = None # Stores dict if category is missing

st.title("💸 AI Expense Manager")

# Create Tabs
tab1, tab2 = st.tabs(["💬 Chat & Entry", "📊 Dashboard"])

# --- TAB 1: CHAT INTERFACE ---
with tab1:
    # Display Chat History
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Handle User Input
    if prompt := st.chat_input("Type here..."):
        # Add user message to state
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Logic Branch: Are we resolving a pending category?
        if st.session_state.pending_expense:
            # The user's input is treated as the category
            category_input = prompt.strip().title()
            
            # Retrieve pending data
            pending = st.session_state.pending_expense
            
            # Save data
            success = save_expense(
                pending['date'], pending['item'], pending['amount'], category_input, pending['notes']
            )
            
            if success:
                response_msg = f"Got it! Categorized as **{category_input}** and saved."
                st.session_state.pending_expense = None # Reset state
            else:
                response_msg = "Something went wrong saving the data."
                
            with st.chat_message("assistant"):
                st.markdown(response_msg)
            st.session_state.messages.append({"role": "assistant", "content": response_msg})

        else:
            # Standard Processing
            with st.spinner("Thinking..."):
                current_df = get_data()
                ai_result = analyze_intent_and_process(prompt, current_df)

            if ai_result.get("intent") == "QUERY":
                response_msg = ai_result.get("response_text")
                with st.chat_message("assistant"):
                    st.markdown(response_msg)
                st.session_state.messages.append({"role": "assistant", "content": response_msg})

            elif ai_result.get("intent") == "LOG_EXPENSE":
                data = ai_result
                
                # Check for uncertain category
                if data.get("category") == "UNCERTAIN":
                    # Store in session state and ask user
                    st.session_state.pending_expense = data
                    response_msg = f"I noticed you spent **₹{data['amount']}** on **{data['item']}**, but I'm not sure about the category. Could you tell me which category this belongs to?"
                    
                    with st.chat_message("assistant"):
                        st.markdown(response_msg)
                    st.session_state.messages.append({"role": "assistant", "content": response_msg})
                else:
                    # Save immediately
                    success = save_expense(
                        data['date'], data['item'], data['amount'], data['category'], data['notes']
                    )
                    if success:
                        response_msg = f"✅ Saved: **₹{data['amount']}** for **{data['item']}** ({data['category']})."
                    else:
                        response_msg = "Failed to save to Google Sheets."
                        
                    with st.chat_message("assistant"):
                        st.markdown(response_msg)
                    st.session_state.messages.append({"role": "assistant", "content": response_msg})
            
            else:
                # Error or confused
                response_msg = "I'm sorry, I didn't understand that. Please try again."
                if "response_text" in ai_result:
                    response_msg += f"\nDebug: {ai_result['response_text']}"
                
                with st.chat_message("assistant"):
                    st.markdown(response_msg)
                st.session_state.messages.append({"role": "assistant", "content": response_msg})

# --- TAB 2: DASHBOARD ---
with tab2:
    st.header("Spending Overview")
    
    df = get_data()
    
    if not df.empty:
        # --- ROBUST DATA CLEANUP (Prevents Crashes) ---
        # 1. Force Amount to be numeric (turn "100" into 100.0)
        df["Amount"] = pd.to_numeric(df["Amount"], errors='coerce').fillna(0.0)
        
        # 2. Force Category to be string (prevents mixed type errors)
        df["Category"] = df["Category"].fillna("Uncategorized").astype(str)
        
        # 3. Handle Dates
        df["Date"] = pd.to_datetime(df["Date"], errors='coerce')

        # --- METRICS SECTION ---
        col1, col2 = st.columns(2)
        total_spent = df["Amount"].sum()
        
        with col1:
            st.metric("Total Spent (All Time)", f"₹{total_spent:,.2f}")
        
        with col2:
            # Safe month calculation
            if not df["Date"].isna().all():
                current_month = datetime.now().month
                current_year = datetime.now().year
                monthly_mask = (df["Date"].dt.month == current_month) & (df["Date"].dt.year == current_year)
                monthly_spent = df.loc[monthly_mask, "Amount"].sum()
                st.metric("This Month", f"₹{monthly_spent:,.2f}")
            else:
                st.metric("This Month", "₹0.00")

        st.divider()

        # --- CHART SECTION (With Safety Try/Except) ---
        st.subheader("Expenses by Category")
        
        try:
            # Group data safely
            cat_group = df.groupby("Category")["Amount"].sum()
            
            # We use a Bar Chart because it is 10x more stable than Pie Chart on Cloud
            if not cat_group.empty and cat_group.sum() > 0:
                st.bar_chart(cat_group)
            else:
                st.info("Add some expenses to see your spending breakdown!")
                
        except Exception as e:
            # If chart crashes, show this error but KEEP APP RUNNING
            st.warning(f"Could not render chart (Data Issue): {e}")
            st.write("Here is your raw data instead:")
            st.dataframe(df)

        # --- RECENT TRANSACTIONS ---
        st.divider()
        st.subheader("Recent Transactions")
        
        # Display latest 5 items
        st.dataframe(
            df.sort_values(by="Date", ascending=False).head(5), 
            use_container_width=True
        )
        
    else:
        st.info("No data found in Google Sheets yet. Go to the Chat tab to add expenses!")
