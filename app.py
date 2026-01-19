import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import re

# Configure Streamlit to allow large file uploads (up to 1GB)
st.set_page_config(page_title="Cashback Analysis Dashboard", layout="wide", page_icon="💰")

# Set max upload size to 1GB (1024 MB)
# This needs to be set via config.toml file or command line

# Helper Functions
def parse_order_history(order_history_str):
    """Parse the order history string into a list of dictionaries"""
    orders = []
    order_history_str = str(order_history_str)
    order_pattern = r'\{:order_id[^}]+\}'
    order_matches = re.findall(order_pattern, order_history_str)
    
    for order_str in order_matches:
        order = {}
        order_id_match = re.search(r':order_id\s+["\']?([#A-Z0-9]+)["\']?', order_str)
        order['order_id'] = order_id_match.group(1) if order_id_match else 'N/A'
        
        date_match = re.search(r':order_date\s+#t\s+["\']([^"\']+)["\']', order_str)
        order['order_date'] = date_match.group(1) if date_match else 'N/A'
        
        silver_match = re.search(r':silver_revenue\s+([\d,]+\.?\d*)M?', order_str)
        order['silver_revenue'] = float(silver_match.group(1).replace(',', '')) if silver_match else 0.0
        
        gold_match = re.search(r':gold_revenue\s+([\d,]+\.?\d*)M?', order_str)
        order['gold_revenue'] = float(gold_match.group(1).replace(',', '')) if gold_match else 0.0
        
        promo_match = re.search(r':promo_amount\s+([\d,]+\.?\d*)M?', order_str)
        order['promo_amount'] = float(promo_match.group(1).replace(',', '')) if promo_match else 0.0
        
        orders.append(order)
    
    return orders

def get_ltv_bracket(ltv, brackets):
    """Determine LTV bracket"""
    for bracket in brackets:
        if ltv < bracket['max']:
            return bracket['label']
    return brackets[-1]['label']

def calculate_cashback_for_month(df, target_month, cashback_config, ltv_brackets, expiry_days, use_ltv=False):
    """Calculate cashback for a specific month"""
    
    # Initialize results
    if use_ltv:
        # Results per LTV bracket
        results = {bracket['label']: {
            'total_silver_rev': 0,
            'total_gold_rev': 0,
            'total_promo': 0,
            'total_silver_cb': 0,
            'total_gold_cb': 0,
            'total_coins_used': 0,
            'total_coin_balance': 0,
            'unique_customers': set()
        } for bracket in ltv_brackets}
    else:
        # Single result for the month
        results = {
            'total_silver_rev': 0,
            'total_gold_rev': 0,
            'total_promo': 0,
            'total_silver_cb': 0,
            'total_gold_cb': 0,
            'total_coins_used': 0,
            'total_coin_balance': 0,
            'unique_customers': set()
        }
    
    # Process each customer
    for idx, row in df.iterrows():
        customer_id = row['customer_id']
        order_history = parse_order_history(row['order_history'])
        order_history.sort(key=lambda x: x['order_date'])
        
        wallet_transactions = []
        cumulative_ltv = 0
        order_num = 0
        customer_contributed = False
        
        for order in order_history:
            try:
                order_date = datetime.strptime(order['order_date'], '%Y-%m-%d')
            except:
                continue
            
            order_num += 1
            month_key = order_date.strftime('%Y-%m')
            is_target_month = (month_key == target_month)
            
            # Get current bracket based on cumulative LTV
            current_bracket = get_ltv_bracket(cumulative_ltv, ltv_brackets)
            config = cashback_config[current_bracket]
            
            # Remove expired coins
            wallet_transactions = [
                txn for txn in wallet_transactions 
                if (order_date - txn['earned_date']).days <= expiry_days
            ]
            
            wallet_balance = sum(txn['balance'] for txn in wallet_transactions)
            order_value = order['silver_revenue'] + order['gold_revenue']
            
            # Calculate cashback using bracket-specific rates
            silver_cashback = order['silver_revenue'] * (config['silver_cb'] / 100)
            gold_cashback = order['gold_revenue'] * (config['gold_cb'] / 100)
            total_cashback = silver_cashback + gold_cashback
            
            # Calculate coins used
            coins_used = 0
            if order_num > 1:
                max_usable = order_value * (config['redeem_pct'] / 100)
                coins_to_use = min(wallet_balance, max_usable)
                coins_used = coins_to_use
                
                # Deduct from wallet (FIFO)
                remaining = coins_to_use
                for txn in wallet_transactions:
                    if remaining <= 0:
                        break
                    deduction = min(txn['balance'], remaining)
                    txn['balance'] -= deduction
                    remaining -= deduction
            
            # Add new cashback to wallet
            if total_cashback > 0:
                wallet_transactions.append({
                    'balance': total_cashback,
                    'earned_date': order_date
                })
            
            # Update cumulative LTV
            amount_paid = order_value - coins_used - order['promo_amount']
            cumulative_ltv += amount_paid
            
            # If this order is in target month, accumulate metrics
            if is_target_month:
                customer_contributed = True
                
                if use_ltv:
                    results[current_bracket]['total_silver_rev'] += order['silver_revenue']
                    results[current_bracket]['total_gold_rev'] += order['gold_revenue']
                    results[current_bracket]['total_promo'] += order['promo_amount']
                    results[current_bracket]['total_silver_cb'] += silver_cashback
                    results[current_bracket]['total_gold_cb'] += gold_cashback
                    results[current_bracket]['total_coins_used'] += coins_used
                    results[current_bracket]['unique_customers'].add(customer_id)
                else:
                    results['total_silver_rev'] += order['silver_revenue']
                    results['total_gold_rev'] += order['gold_revenue']
                    results['total_promo'] += order['promo_amount']
                    results['total_silver_cb'] += silver_cashback
                    results['total_gold_cb'] += gold_cashback
                    results['total_coins_used'] += coins_used
                    results['unique_customers'].add(customer_id)
        
        # Calculate final wallet balance after all orders
        final_wallet = sum(txn['balance'] for txn in wallet_transactions)
        
        # Add customer's final balance if they had orders in target month
        if customer_contributed:
            if use_ltv:
                # Add to the bracket they ended up in
                final_bracket = get_ltv_bracket(cumulative_ltv, ltv_brackets)
                results[final_bracket]['total_coin_balance'] += final_wallet
            else:
                results['total_coin_balance'] += final_wallet
    
    return results

def calculate_cashback(df, cashback_config, ltv_brackets, expiry_days):
    """Calculate cashback with expiry logic"""
    customer_results = {}
    monthly_results = []
    
    for idx, row in df.iterrows():
        customer_id = row['customer_id']
        order_history = parse_order_history(row['order_history'])
        order_history.sort(key=lambda x: x['order_date'])
        
        wallet_transactions = []
        cumulative_ltv = 0
        total_silver_rev = 0
        total_gold_rev = 0
        total_promo = 0
        total_silver_cb = 0
        total_gold_cb = 0
        total_coins_used = 0
        repeat_revenue = 0
        
        for order_num, order in enumerate(order_history, 1):
            try:
                order_date = datetime.strptime(order['order_date'], '%Y-%m-%d')
            except:
                continue
            
            # Get current bracket
            current_bracket = get_ltv_bracket(cumulative_ltv, ltv_brackets)
            config = cashback_config[current_bracket]
            
            # Remove expired coins
            wallet_transactions = [
                txn for txn in wallet_transactions 
                if (order_date - txn['earned_date']).days <= expiry_days
            ]
            
            wallet_balance = sum(txn['balance'] for txn in wallet_transactions)
            order_value = order['silver_revenue'] + order['gold_revenue']
            
            # Calculate cashback
            silver_cashback = order['silver_revenue'] * (config['silver_cb'] / 100)
            gold_cashback = order['gold_revenue'] * (config['gold_cb'] / 100)
            total_cashback = silver_cashback + gold_cashback
            
            # Calculate coins used
            coins_used = 0
            if order_num > 1:
                max_usable = order_value * (config['redeem_pct'] / 100)
                coins_to_use = min(wallet_balance, max_usable)
                coins_used = coins_to_use
                
                # Deduct from wallet (FIFO)
                remaining = coins_to_use
                for txn in wallet_transactions:
                    if remaining <= 0:
                        break
                    deduction = min(txn['balance'], remaining)
                    txn['balance'] -= deduction
                    remaining -= deduction
            
            # Add new cashback
            if total_cashback > 0:
                wallet_transactions.append({
                    'balance': total_cashback,
                    'earned_date': order_date,
                    'expiry_days': expiry_days
                })
            
            amount_paid = order_value - coins_used - order['promo_amount']
            cumulative_ltv += amount_paid
            
            if order_num > 1:
                repeat_revenue += order_value
            
            total_silver_rev += order['silver_revenue']
            total_gold_rev += order['gold_revenue']
            total_promo += order['promo_amount']
            total_silver_cb += silver_cashback
            total_gold_cb += gold_cashback
            total_coins_used += coins_used
            
            # Monthly tracking
            month_key = order_date.strftime('%Y-%m')
            monthly_results.append({
                'customer_id': customer_id,
                'month': month_key,
                'order_date': order_date,
                'ltv_bracket': current_bracket,
                'silver_rev': order['silver_revenue'],
                'gold_rev': order['gold_revenue'],
                'silver_cb': silver_cashback,
                'gold_cb': gold_cashback,
                'coins_used': coins_used,
                'wallet_balance': sum(txn['balance'] for txn in wallet_transactions)
            })
        
        final_wallet = sum(txn['balance'] for txn in wallet_transactions)
        
        customer_results[customer_id] = {
            'final_ltv': cumulative_ltv,
            'total_silver_rev': total_silver_rev,
            'total_gold_rev': total_gold_rev,
            'repeat_revenue': repeat_revenue,
            'total_promo': total_promo,
            'total_silver_cb': total_silver_cb,
            'total_gold_cb': total_gold_cb,
            'total_coins_used': total_coins_used,
            'final_wallet_balance': final_wallet,
            'num_orders': len(order_history)
        }
    
    return customer_results, pd.DataFrame(monthly_results)

def create_summary_by_ltv(customer_results, ltv_brackets):
    """Create summary grouped by LTV brackets"""
    summary = []
    
    for bracket in ltv_brackets:
        bracket_customers = {
            cid: data for cid, data in customer_results.items() 
            if get_ltv_bracket(data['final_ltv'], ltv_brackets) == bracket['label']
        }
        
        if not bracket_customers:
            summary.append({
                'LTV_Bracket': bracket['label'],
                'Users': 0,
                'Gold_Revenue': 0.0,
                'Silver_Revenue': 0.0,
                'Repeat_Revenue': 0.0,
                'Actual_Promo': 0.0,
                'Silver_CB': 0.0,
                'Gold_CB': 0.0,
                'CB_Redeemed': 0.0,
                'Total_Discount': 0.0,
                'Coin_Balance': 0.0
            })
            continue
        
        summary.append({
            'LTV_Bracket': bracket['label'],
            'Users': len(bracket_customers),
            'Gold_Revenue': sum(d['total_gold_rev'] for d in bracket_customers.values()),
            'Silver_Revenue': sum(d['total_silver_rev'] for d in bracket_customers.values()),
            'Repeat_Revenue': sum(d['repeat_revenue'] for d in bracket_customers.values()),
            'Actual_Promo': sum(d['total_promo'] for d in bracket_customers.values()),
            'Silver_CB': sum(d['total_silver_cb'] for d in bracket_customers.values()),
            'Gold_CB': sum(d['total_gold_cb'] for d in bracket_customers.values()),
            'CB_Redeemed': sum(d['total_coins_used'] for d in bracket_customers.values()),
            'Total_Discount': sum(d['total_promo'] + d['total_coins_used'] for d in bracket_customers.values()),
            'Coin_Balance': sum(d['final_wallet_balance'] for d in bracket_customers.values())
        })
    
    return pd.DataFrame(summary)

def create_monthly_summary(monthly_df, use_ltv=False):
    """Create monthly summary"""
    if use_ltv:
        grouped = monthly_df.groupby(['month', 'ltv_bracket']).agg({
            'customer_id': 'nunique',
            'silver_rev': 'sum',
            'gold_rev': 'sum',
            'silver_cb': 'sum',
            'gold_cb': 'sum',
            'coins_used': 'sum',
            'wallet_balance': 'last'
        }).reset_index()
    else:
        grouped = monthly_df.groupby('month').agg({
            'customer_id': 'nunique',
            'silver_rev': 'sum',
            'gold_rev': 'sum',
            'silver_cb': 'sum',
            'gold_cb': 'sum',
            'coins_used': 'sum',
            'wallet_balance': 'last'
        }).reset_index()
    
    grouped.columns = ['Month'] + (['LTV_Bracket'] if use_ltv else []) + ['Users', 'Silver_Revenue', 'Gold_Revenue', 
                      'Silver_CB', 'Gold_CB', 'CB_Redeemed', 'Wallet_Balance']
    return grouped

# Streamlit UI
st.title("💰 Cashback Analysis Dashboard")
st.markdown("---")

# Sidebar Configuration
with st.sidebar:
    st.header("⚙️ Configuration")
    
    # File Upload
    st.subheader("📁 Data Upload")
    uploaded_file = st.file_uploader("Upload CSV file", type=['csv'])
    
    st.markdown("---")
    
    # View Mode Selection
    st.subheader("📊 View Mode")
    view_mode = st.radio("Analysis Type", ["LTV Brackets", "Monthly Analysis"])
    
    monthly_ltv_mode = False
    selected_months = []
    
    if view_mode == "Monthly Analysis":
        monthly_ltv_mode = st.checkbox("Show LTV breakdown by month", value=False)
        
        # Month selection
        st.markdown("**Select Months to Analyze:**")
        available_months = st.text_input(
            "Enter months (YYYY-MM format, comma-separated)",
            value="2024-11,2024-12",
            help="Example: 2024-11,2024-12 for November and December 2024"
        )
        selected_months = [m.strip() for m in available_months.split(',') if m.strip()]
    
    st.markdown("---")
    
    # LTV Brackets Configuration
    st.subheader("🎯 LTV Brackets")
    num_brackets = st.number_input("Number of brackets", min_value=3, max_value=10, value=6)
    
    ltv_brackets = []
    with st.expander("Configure LTV Brackets", expanded=False):
        # Default bracket values
        default_brackets = [
            (0, 5000), (5000, 10000), (10000, 25000), 
            (25000, 50000), (50000, 100000), (100000, float('inf')),
            (200000, float('inf')), (300000, float('inf')), 
            (400000, float('inf')), (500000, float('inf'))
        ]
        
        for i in range(num_brackets):
            st.markdown(f"**Bracket {i+1}**")
            col1, col2 = st.columns(2)
            
            default_min = default_brackets[i][0] if i < len(default_brackets) else i * 10000
            default_max = default_brackets[i][1] if i < len(default_brackets) else (i + 1) * 10000
            
            with col1:
                min_val = st.number_input(
                    f"Min Value", 
                    value=default_min, 
                    key=f"min_{i}",
                    min_value=0,
                    step=1000
                )
            with col2:
                if i == num_brackets - 1:
                    # Last bracket can be infinity
                    max_option = st.selectbox(
                        f"Max Value",
                        options=["Custom", "∞ (Infinity)"],
                        key=f"max_option_{i}"
                    )
                    if max_option == "∞ (Infinity)":
                        max_val = float('inf')
                        st.info("Last bracket extends to infinity")
                    else:
                        max_val = st.number_input(
                            f"Custom Max",
                            value=default_max if default_max != float('inf') else min_val + 10000,
                            key=f"max_{i}",
                            min_value=min_val + 1,
                            step=1000
                        )
                else:
                    max_val = st.number_input(
                        f"Max Value",
                        value=default_max if default_max != float('inf') else min_val + 5000,
                        key=f"max_{i}",
                        min_value=min_val + 1,
                        step=1000
                    )
            
            # Validation
            if i > 0 and min_val < ltv_brackets[i-1]['max'] and ltv_brackets[i-1]['max'] != float('inf'):
                st.warning(f"⚠️ Min value should be >= previous bracket's max ({ltv_brackets[i-1]['max']})")
            
            label = f"{min_val}-{max_val if max_val != float('inf') else '∞'}"
            ltv_brackets.append({'min': min_val, 'max': max_val, 'label': label})
            
            st.markdown("---")
    
    st.markdown("---")
    
    # Cashback Configuration
    st.subheader("💳 Cashback Settings")
    cashback_config = {}
    
    with st.expander("Configure Cashback Rates", expanded=False):
        for bracket in ltv_brackets:
            st.markdown(f"**{bracket['label']}**")
            col1, col2, col3 = st.columns(3)
            with col1:
                silver_cb = st.number_input(f"Silver CB%", value=4.0, step=0.5, key=f"scb_{bracket['label']}")
            with col2:
                gold_cb = st.number_input(f"Gold CB%", value=2.0, step=0.5, key=f"gcb_{bracket['label']}")
            with col3:
                redeem_pct = st.number_input(f"Redeem%", value=20.0, step=5.0, key=f"red_{bracket['label']}")
            
            cashback_config[bracket['label']] = {
                'silver_cb': silver_cb,
                'gold_cb': gold_cb,
                'redeem_pct': redeem_pct
            }
    
    st.markdown("---")
    
    # Expiry Configuration
    st.subheader("⏰ Expiry Settings")
    expiry_days = st.number_input("Coins Expiry (days)", min_value=30, max_value=365, value=180, step=30)
    
    st.markdown("---")
    
    # Run Analysis Button
    run_analysis = st.button("🚀 Run Analysis", type="primary", use_container_width=True)

# Main Content Area
if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    df.columns = df.columns.str.strip()
    
    st.success(f"✅ Loaded {len(df)} customers")
    
    if run_analysis:
        with st.spinner("Analyzing data..."):
            if view_mode == "Monthly Analysis" and selected_months:
                # Monthly analysis mode
                monthly_comparison = []
                
                for target_month in selected_months:
                    month_results = calculate_cashback_for_month(
                        df, target_month, cashback_config, ltv_brackets, 
                        expiry_days, use_ltv=monthly_ltv_mode
                    )
                    
                    if monthly_ltv_mode:
                        # Results per LTV bracket
                        for bracket_label, data in month_results.items():
                            monthly_comparison.append({
                                'Month': target_month,
                                'LTV_Bracket': bracket_label,
                                'Users': len(data['unique_customers']),
                                'Gold_Revenue': data['total_gold_rev'],
                                'Silver_Revenue': data['total_silver_rev'],
                                'Actual_Promo': data['total_promo'],
                                'Silver_CB': data['total_silver_cb'],
                                'Gold_CB': data['total_gold_cb'],
                                'CB_Redeemed': data['total_coins_used'],
                                'Coin_Balance': data['total_coin_balance']
                            })
                    else:
                        # Single result per month
                        monthly_comparison.append({
                            'Month': target_month,
                            'Users': len(month_results['unique_customers']),
                            'Gold_Revenue': month_results['total_gold_rev'],
                            'Silver_Revenue': month_results['total_silver_rev'],
                            'Actual_Promo': month_results['total_promo'],
                            'Silver_CB': month_results['total_silver_cb'],
                            'Gold_CB': month_results['total_gold_cb'],
                            'CB_Redeemed': month_results['total_coins_used'],
                            'Coin_Balance': month_results['total_coin_balance']
                        })
                
                st.session_state['monthly_comparison'] = pd.DataFrame(monthly_comparison)
                st.session_state['view_mode'] = 'monthly'
                st.session_state['monthly_ltv_mode'] = monthly_ltv_mode
            else:
                # LTV analysis mode
                customer_results, monthly_df = calculate_cashback(df, cashback_config, ltv_brackets, expiry_days)
                st.session_state['customer_results'] = customer_results
                st.session_state['monthly_df'] = monthly_df
                st.session_state['ltv_brackets'] = ltv_brackets
                st.session_state['view_mode'] = 'ltv'
    
    # Display Results
    if 'customer_results' in st.session_state or 'monthly_comparison' in st.session_state:
        current_view = st.session_state.get('view_mode', 'ltv')
        
        # Tabs for different views
        tab1, tab2, tab3, tab4 = st.tabs(["📈 Overview", "📊 Detailed Analysis", "📅 Monthly Trends", "📋 Raw Data"])
        
        if current_view == 'monthly':
            # Monthly Analysis Display
            monthly_comparison = st.session_state['monthly_comparison']
            monthly_ltv_mode = st.session_state.get('monthly_ltv_mode', False)
            
            with tab1:
                # KPI Metrics
                col1, col2, col3, col4 = st.columns(4)
                
                total_revenue = monthly_comparison['Silver_Revenue'].sum() + monthly_comparison['Gold_Revenue'].sum()
                total_cb_earned = monthly_comparison['Silver_CB'].sum() + monthly_comparison['Gold_CB'].sum()
                total_cb_redeemed = monthly_comparison['CB_Redeemed'].sum()
                avg_users = monthly_comparison.groupby('Month')['Users'].sum().mean() if monthly_ltv_mode else monthly_comparison['Users'].mean()
                
                col1.metric("Avg Users/Month", f"{avg_users:,.0f}")
                col2.metric("Total Revenue", f"₹{total_revenue:,.0f}")
                col3.metric("CB Earned", f"₹{total_cb_earned:,.0f}")
                col4.metric("CB Redeemed", f"₹{total_cb_redeemed:,.0f}")
                
                st.markdown("---")
                
                if monthly_ltv_mode:
                    st.subheader("Monthly Performance by LTV Bracket")
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        fig = px.bar(monthly_comparison, x='Month', y='Users', color='LTV_Bracket',
                                    title="Users by Month & LTV Bracket", barmode='stack')
                        st.plotly_chart(fig, use_container_width=True)
                    
                    with col2:
                        monthly_comparison['Total_Revenue'] = monthly_comparison['Silver_Revenue'] + monthly_comparison['Gold_Revenue']
                        fig = px.bar(monthly_comparison, x='Month', y='Total_Revenue', color='LTV_Bracket',
                                    title="Revenue by Month & LTV Bracket", barmode='stack')
                        st.plotly_chart(fig, use_container_width=True)
                else:
                    st.subheader("Monthly Performance Overview")
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        fig = px.line(monthly_comparison, x='Month', y='Users',
                                     title="Monthly Users", markers=True)
                        st.plotly_chart(fig, use_container_width=True)
                    
                    with col2:
                        monthly_comparison['Total_Revenue'] = monthly_comparison['Silver_Revenue'] + monthly_comparison['Gold_Revenue']
                        fig = px.line(monthly_comparison, x='Month', y='Total_Revenue',
                                     title="Monthly Revenue", markers=True)
                        st.plotly_chart(fig, use_container_width=True)
                
                col1, col2 = st.columns(2)
                
                with col1:
                    if monthly_ltv_mode:
                        monthly_agg = monthly_comparison.groupby('Month').agg({
                            'Silver_CB': 'sum',
                            'Gold_CB': 'sum',
                            'CB_Redeemed': 'sum'
                        }).reset_index()
                        monthly_agg['Total_CB'] = monthly_agg['Silver_CB'] + monthly_agg['Gold_CB']
                    else:
                        monthly_agg = monthly_comparison.copy()
                        monthly_agg['Total_CB'] = monthly_agg['Silver_CB'] + monthly_agg['Gold_CB']
                    
                    fig = go.Figure()
                    fig.add_trace(go.Bar(x=monthly_agg['Month'], y=monthly_agg['Total_CB'], name='CB Earned'))
                    fig.add_trace(go.Bar(x=monthly_agg['Month'], y=monthly_agg['CB_Redeemed'], name='CB Redeemed'))
                    fig.update_layout(title="Cashback Earned vs Redeemed by Month", barmode='group')
                    st.plotly_chart(fig, use_container_width=True)
                
                with col2:
                    fig = px.bar(monthly_comparison, x='Month', y='Coin_Balance',
                                title="Wallet Balance by Month", 
                                color='LTV_Bracket' if monthly_ltv_mode else None)
                    st.plotly_chart(fig, use_container_width=True)
            
            with tab2:
                st.subheader("Monthly Analysis Details")
                
                # Add calculated columns
                monthly_comparison['Total_CB_Earned'] = monthly_comparison['Silver_CB'] + monthly_comparison['Gold_CB']
                monthly_comparison['Redemption_Rate'] = (monthly_comparison['CB_Redeemed'] / monthly_comparison['Total_CB_Earned'] * 100).fillna(0).round(2)
                monthly_comparison['Total_Revenue'] = monthly_comparison['Silver_Revenue'] + monthly_comparison['Gold_Revenue']
                
                st.dataframe(monthly_comparison, use_container_width=True, height=400)
                
                csv = monthly_comparison.to_csv(index=False)
                st.download_button("📥 Download Monthly Analysis", csv, "monthly_analysis.csv", "text/csv")
            
            with tab3:
                st.subheader("📅 Monthly Trends & Insights")
                
                monthly_agg = monthly_comparison.groupby('Month').agg({
                    'Users': 'sum' if monthly_ltv_mode else 'first',
                    'Silver_Revenue': 'sum',
                    'Gold_Revenue': 'sum',
                    'Silver_CB': 'sum',
                    'Gold_CB': 'sum',
                    'CB_Redeemed': 'sum',
                    'Coin_Balance': 'sum'
                }).reset_index()
                
                monthly_agg['Total_CB'] = monthly_agg['Silver_CB'] + monthly_agg['Gold_CB']
                monthly_agg['CB_Rate'] = (monthly_agg['CB_Redeemed'] / monthly_agg['Total_CB'] * 100).fillna(0).round(2)
                
                col1, col2 = st.columns(2)
                
                with col1:
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=monthly_agg['Month'], y=monthly_agg['Total_CB'],
                                            mode='lines+markers', name='CB Earned'))
                    fig.add_trace(go.Scatter(x=monthly_agg['Month'], y=monthly_agg['CB_Redeemed'],
                                            mode='lines+markers', name='CB Redeemed'))
                    fig.update_layout(title="Cashback Trends", xaxis_title="Month", yaxis_title="Amount (₹)")
                    st.plotly_chart(fig, use_container_width=True)
                
                with col2:
                    fig = px.line(monthly_agg, x='Month', y='CB_Rate',
                                 title="Redemption Rate (%)", markers=True)
                    st.plotly_chart(fig, use_container_width=True)
                
                st.dataframe(monthly_agg, use_container_width=True)
            
            with tab4:
                st.subheader("Raw Monthly Data")
                st.dataframe(monthly_comparison, use_container_width=True, height=400)
                
                csv = monthly_comparison.to_csv(index=False)
                st.download_button("📥 Download Raw Data", csv, "monthly_raw_data.csv", "text/csv")
        
        else:
            # LTV Analysis Display
            customer_results = st.session_state['customer_results']
            monthly_df = st.session_state['monthly_df']
            ltv_brackets = st.session_state['ltv_brackets']
        
        with tab1:
            # KPI Metrics
            col1, col2, col3, col4 = st.columns(4)
            
            total_users = len(customer_results)
            total_revenue = sum(d['total_silver_rev'] + d['total_gold_rev'] for d in customer_results.values())
            total_cb_earned = sum(d['total_silver_cb'] + d['total_gold_cb'] for d in customer_results.values())
            total_cb_redeemed = sum(d['total_coins_used'] for d in customer_results.values())
            
            col1.metric("Total Users", f"{total_users:,}")
            col2.metric("Total Revenue", f"₹{total_revenue:,.0f}")
            col3.metric("CB Earned", f"₹{total_cb_earned:,.0f}")
            col4.metric("CB Redeemed", f"₹{total_cb_redeemed:,.0f}", 
                       delta=f"{(total_cb_redeemed/total_cb_earned*100):.1f}% redemption")
            
            st.markdown("---")
            
            # LTV Summary
            summary_df = create_summary_by_ltv(customer_results, ltv_brackets)
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("Users by LTV Bracket")
                fig = px.bar(summary_df, x='LTV_Bracket', y='Users', 
                            title="User Distribution", color='Users',
                            color_continuous_scale='Blues')
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                st.subheader("Revenue by LTV Bracket")
                summary_df['Total_Revenue'] = summary_df['Silver_Revenue'] + summary_df['Gold_Revenue']
                fig = px.bar(summary_df, x='LTV_Bracket', y='Total_Revenue',
                            title="Total Revenue", color='Total_Revenue',
                            color_continuous_scale='Greens')
                st.plotly_chart(fig, use_container_width=True)
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("Cashback Economics")
                fig = go.Figure()
                fig.add_trace(go.Bar(x=summary_df['LTV_Bracket'], 
                                    y=summary_df['Silver_CB'] + summary_df['Gold_CB'],
                                    name='CB Earned'))
                fig.add_trace(go.Bar(x=summary_df['LTV_Bracket'], 
                                    y=summary_df['CB_Redeemed'],
                                    name='CB Redeemed'))
                fig.update_layout(title="Cashback Earned vs Redeemed", barmode='group')
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                st.subheader("Wallet Balance by Bracket")
                fig = px.pie(summary_df, values='Coin_Balance', names='LTV_Bracket',
                            title="Outstanding Wallet Balance Distribution")
                st.plotly_chart(fig, use_container_width=True)
        
        with tab2:
            summary_df = create_summary_by_ltv(customer_results, ltv_brackets)
            st.subheader("Detailed LTV Bracket Analysis")
            
            # Add calculated columns
            summary_df['Total_CB_Earned'] = summary_df['Silver_CB'] + summary_df['Gold_CB']
            summary_df['Redemption_Rate'] = (summary_df['CB_Redeemed'] / summary_df['Total_CB_Earned'] * 100).round(2)
            summary_df['Avg_Revenue_Per_User'] = (summary_df['Silver_Revenue'] + summary_df['Gold_Revenue']) / summary_df['Users'].replace(0, 1)
            
            st.dataframe(summary_df, use_container_width=True, height=400)
            
            # Download button
            csv = summary_df.to_csv(index=False)
            st.download_button("📥 Download Summary CSV", csv, "ltv_summary.csv", "text/csv")
        
        with tab3:
            st.subheader("📅 Monthly Trends & Insights")
            
            monthly_summary = create_monthly_summary(monthly_df, use_ltv=False)
            
            monthly_summary['Total_CB'] = monthly_summary['Silver_CB'] + monthly_summary['Gold_CB']
            monthly_summary['CB_Rate'] = (monthly_summary['CB_Redeemed'] / monthly_summary['Total_CB'] * 100).round(2)
            
            col1, col2 = st.columns(2)
            
            with col1:
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=monthly_summary['Month'], y=monthly_summary['Total_CB'],
                                        mode='lines+markers', name='CB Earned'))
                fig.add_trace(go.Scatter(x=monthly_summary['Month'], y=monthly_summary['CB_Redeemed'],
                                        mode='lines+markers', name='CB Redeemed'))
                fig.update_layout(title="Monthly Cashback Trends", xaxis_title="Month", yaxis_title="Amount (₹)")
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                fig = px.line(monthly_summary, x='Month', y='CB_Rate',
                             title="Monthly Redemption Rate (%)", markers=True)
                st.plotly_chart(fig, use_container_width=True)
        
        with tab4:
            st.subheader("Raw Customer Data")
            
            # Create customer-level dataframe
            customer_df = pd.DataFrame([
                {
                    'Customer_ID': cid,
                    'Final_LTV': data['final_ltv'],
                    'LTV_Bracket': get_ltv_bracket(data['final_ltv'], ltv_brackets),
                    'Num_Orders': data['num_orders'],
                    'Total_Silver_Rev': data['total_silver_rev'],
                    'Total_Gold_Rev': data['total_gold_rev'],
                    'Silver_CB_Earned': data['total_silver_cb'],
                    'Gold_CB_Earned': data['total_gold_cb'],
                    'CB_Redeemed': data['total_coins_used'],
                    'Wallet_Balance': data['final_wallet_balance']
                }
                for cid, data in customer_results.items()
            ])
            
            st.dataframe(customer_df, use_container_width=True, height=400)
            
            csv = customer_df.to_csv(index=False)
            st.download_button("📥 Download Customer Data", csv, "customer_data.csv", "text/csv")

else:
    st.info("👈 Please upload a CSV file to begin analysis")
    
    st.markdown("### 📝 Dashboard Features:")
    st.markdown("""
    - **LTV Bracket Analysis**: Analyze customers by lifetime value segments
    - **Monthly Analysis**: Track performance over time
    - **Configurable Parameters**: 
        - Customize LTV brackets
        - Set cashback rates per bracket
        - Configure expiry periods
        - Toggle monthly LTV breakdown
    - **Interactive Visualizations**: Charts and graphs for easy insights
    - **Export Results**: Download analysis as CSV files
    """)