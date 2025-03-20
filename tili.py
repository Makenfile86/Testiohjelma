import os
import json
import sqlite3
import datetime
from pathlib import Path
from flask import Flask, Blueprint, render_template, request, redirect, url_for, flash, abort, g, jsonify

# Create a Blueprint for account routes
tili_bp = Blueprint('tili', __name__, template_folder='templates')

def get_db_connection(db_path):
    """Create a connection to the specified database file"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name
    return conn

@tili_bp.route('/db/<filename>/balances')
def list_balances(filename):
    """List all account balances in a database"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get all accounts
    cursor.execute("""
        SELECT numero, tyyppi, json FROM Tili
        ORDER BY numero
    """)
    
    accounts = []
    for row in cursor.fetchall():
        json_data = json.loads(row['json']) if row['json'] else {}
        account = {
            'numero': row['numero'],
            'nimi': json_data.get('nimi', f"Account {row['numero']}"),
            'tyyppi': row['tyyppi'],
            'tyyppi_nimi': get_account_type_name(row['tyyppi']),
            'balance': 0  # Will be calculated below
        }
        accounts.append(account)
    
    # Calculate balance for each account
    cursor.execute("""
        SELECT tili, SUM(debetsnt) as total_debit, SUM(kreditsnt) as total_credit 
        FROM Vienti 
        GROUP BY tili
    """)
    
    balances = {}
    for row in cursor.fetchall():
        account_number = row['tili']
        debit = row['total_debit'] or 0
        credit = row['total_credit'] or 0
        balance_cents = debit - credit
        balances[account_number] = balance_cents
    
    # Update account balances and filter out zero balances
    non_zero_accounts = []
    for account in accounts:
        balance_cents = balances.get(account['numero'], 0)
        account['balance'] = balance_cents / 100  # Convert cents to euros
        account['balance_formatted'] = f"{abs(account['balance']):.2f}"
        
        # Only include accounts with non-zero balances
        if balance_cents != 0:
            non_zero_accounts.append(account)
    
    # Get client name
    client_name = ""
    try:
        cursor.execute("SELECT arvo FROM Asetus WHERE avain='Nimi'")
        result = cursor.fetchone()
        if result:
            client_name = result[0]
    except:
        pass
    
    conn.close()
    
    # Calculate summary totals by account type
    account_type_totals = calculate_totals_by_type(non_zero_accounts)
    
    # Calculate balance sheet and income statement summary
    summary_totals = calculate_summary_totals(account_type_totals)
    
    return render_template('accounts/balances.html', 
                           accounts=non_zero_accounts, 
                           filename=filename, 
                           client_name=client_name,
                           account_type_totals=account_type_totals['formatted'],
                           summary_totals=summary_totals['formatted'])

@tili_bp.route('/db/<filename>/account/<int:account_number>/transactions')
def account_transactions(filename, account_number):
    """Display all transactions for a specific account"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get account details
    cursor.execute("""
        SELECT numero, tyyppi, json FROM Tili
        WHERE numero = ?
    """, (account_number,))
    
    account_row = cursor.fetchone()
    if not account_row:
        conn.close()
        flash("Account not found", "error")
        return redirect(url_for('tili.list_balances', filename=filename))
    
    # Parse account details
    json_data = json.loads(account_row['json']) if account_row['json'] else {}
    account = {
        'numero': account_row['numero'],
        'nimi': json_data.get('nimi', f"Account {account_row['numero']}"),
        'tyyppi': account_row['tyyppi'],
        'tyyppi_nimi': get_account_type_name(account_row['tyyppi'])
    }
    
    # Get all transactions for this account
    cursor.execute("""
        SELECT v.id, v.tosite, v.pvm, v.selite, v.debetsnt, v.kreditsnt, 
               t.otsikko as voucher_title, t.tyyppi as voucher_type
        FROM Vienti v
        JOIN Tosite t ON v.tosite = t.id
        WHERE v.tili = ?
        ORDER BY v.pvm DESC, v.tosite DESC
    """, (account_number,))
    
    transactions = []
    for row in cursor.fetchall():
        debit_amount = row['debetsnt'] / 100 if row['debetsnt'] else 0
        credit_amount = row['kreditsnt'] / 100 if row['kreditsnt'] else 0
        
        transactions.append({
            'id': row['id'],
            'voucher_id': row['tosite'],
            'voucher_title': row['voucher_title'],
            'voucher_type': row['voucher_type'],
            'date': row['pvm'],
            'description': row['selite'],
            'debit': debit_amount,
            'credit': credit_amount,
            'debit_formatted': f"{debit_amount:.2f}" if debit_amount > 0 else "-",
            'credit_formatted': f"{credit_amount:.2f}" if credit_amount > 0 else "-"
        })
    
    # Calculate running balance
    running_balances = []
    balance = 0
    
    # Account type determines whether debits increase or decrease the balance
    if account['tyyppi'] in ['A', 'E']:  # Assets and Expenses are increased by debits
        for t in reversed(transactions):
            balance += t['debit'] - t['credit']
            running_balances.insert(0, balance)
    else:  # Liabilities, Equity, and Income are increased by credits
        for t in reversed(transactions):
            balance += t['credit'] - t['debit']
            running_balances.insert(0, balance)
    
    # Add running balance to transactions
    for i, t in enumerate(transactions):
        t['balance'] = running_balances[i]
        t['balance_formatted'] = f"{abs(t['balance']):.2f}"
        t['is_debit'] = t['balance'] > 0 if account['tyyppi'] in ['A', 'E'] else t['balance'] < 0
    
    # Get client name
    client_name = ""
    try:
        cursor.execute("SELECT arvo FROM Asetus WHERE avain='Nimi'")
        result = cursor.fetchone()
        if result:
            client_name = result[0]
    except:
        pass
    
    conn.close()
    
    return render_template('accounts/account_transactions.html', 
                           account=account,
                           transactions=transactions,
                           filename=filename,
                           client_name=client_name)

def get_account_type_name(type_code):
    """Return human-readable name for account type code"""
    account_types = {
        'A': 'Asset',
        'B': 'Liability',
        'C': 'Equity',
        'D': 'Income',
        'E': 'Expense'
    }
    return account_types.get(type_code, 'Other')

def calculate_totals_by_type(accounts):
    """Calculate total balances by account type"""
    totals = {
        'Asset': 0,
        'Liability': 0,
        'Equity': 0,
        'Income': 0, 
        'Expense': 0,
        'Other': 0
    }
    
    for account in accounts:
        account_type = account['tyyppi_nimi']
        # Handle different account types with their natural balances
        # Assets and Expenses normally have debit balances (positive)
        # Liabilities, Equity, and Income normally have credit balances (negative)
        if account_type in ['Asset', 'Expense']:
            totals[account_type] += account['balance']
        elif account_type in ['Liability', 'Equity', 'Income']:
            totals[account_type] += abs(account['balance'])  # Use absolute value for credit-normal accounts
        else:
            totals[account_type] += account['balance']
    
    # Create formatted version of the totals for display
    formatted_totals = {}
    for key, value in totals.items():
        if value == 0:
            # Show a small indicator value instead of 0.00 for empty accounts
            formatted_totals[key] = "0.00"
        else:
            formatted_totals[key] = f"{abs(value):.2f}"
    
    # Return both raw values and formatted values
    return {
        'raw': totals,
        'formatted': formatted_totals
    }

def calculate_summary_totals(type_totals):
    """Calculate summary totals for balance sheet and income statement"""
    raw = type_totals['raw']
    
    summary = {
        'total_assets': raw['Asset'],
        'total_liabilities': raw['Liability'],
        'total_equity': raw['Equity'],
        'total_liab_equity': raw['Liability'] + raw['Equity'],
        'total_income': raw['Income'],
        'total_expenses': raw['Expense'],
        'net_income': raw['Income'] - raw['Expense']
    }
    
    # Format for display
    formatted = {}
    for key, value in summary.items():
        if value == 0:
            formatted[key] = "0.00"
        else:
            formatted[key] = f"{abs(value):.2f}"
    
    return {
        'raw': summary,
        'formatted': formatted
    }

def register_blueprint(app):
    """Register the blueprint with the main Flask app"""
    app.register_blueprint(tili_bp) 