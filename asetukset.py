import os
import json
import sqlite3
import datetime
from pathlib import Path
from flask import Flask, Blueprint, render_template, request, redirect, url_for, flash, abort, g, jsonify

# Create a Blueprint for settings routes
asetukset_bp = Blueprint('asetukset', __name__, template_folder='templates')

def get_db_connection(db_path):
    """Create a connection to the specified database file"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name
    return conn

@asetukset_bp.route('/db/<filename>/settings')
def settings_main(filename):
    """Main settings page"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    # Get client name
    client_name = ""
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT arvo FROM Asetus WHERE avain='Nimi'")
        result = cursor.fetchone()
        if result:
            client_name = result[0]
        conn.close()
    except:
        pass
    
    return render_template('settings/main.html', 
                          filename=filename, 
                          client_name=client_name)

@asetukset_bp.route('/db/<filename>/settings/tililuettelo')
def chart_of_accounts(filename):
    """Chart of accounts (tililuettelo) page"""
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
            'alv': json_data.get('alvprosentti', ''),
            'alvkoodi': json_data.get('alvkoodi', ''),
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
    
    # Update account balances
    for account in accounts:
        balance_cents = balances.get(account['numero'], 0)
        account['balance'] = balance_cents / 100  # Convert cents to euros
        account['balance_formatted'] = f"{abs(account['balance']):.2f}"
        account['is_debit'] = balance_cents > 0
    
    # Group accounts by type for better organization
    grouped_accounts = {
        'A': {'name': 'Assets', 'accounts': []},
        'B': {'name': 'Liabilities', 'accounts': []},
        'C': {'name': 'Equity', 'accounts': []},
        'D': {'name': 'Income', 'accounts': []},
        'E': {'name': 'Expense', 'accounts': []},
        'Other': {'name': 'Other', 'accounts': []}
    }
    
    for account in accounts:
        type_code = account['tyyppi']
        if type_code in grouped_accounts:
            grouped_accounts[type_code]['accounts'].append(account)
        else:
            grouped_accounts['Other']['accounts'].append(account)
    
    # Get client name
    client_name = ""
    try:
        cursor.execute("SELECT arvo FROM Asetus WHERE avain='Nimi'")
        result = cursor.fetchone()
        if result:
            client_name = result[0]
    except:
        pass
    
    # Get VAT codes for reference
    vat_codes = get_vat_codes()
    
    conn.close()
    
    return render_template('settings/tililuettelo.html', 
                          grouped_accounts=grouped_accounts,
                          filename=filename,
                          client_name=client_name,
                          vat_codes=vat_codes)

@asetukset_bp.route('/db/<filename>/settings/account/<int:account_number>/edit', methods=['GET', 'POST'])
def edit_account(filename, account_number):
    """Edit an account"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        # Get form data
        name = request.form.get('name', '')
        account_type = request.form.get('type', '')
        vat_percent = request.form.get('vat_percent', '')
        vat_code = request.form.get('vat_code', '')
        
        try:
            # Get current JSON data
            cursor.execute("SELECT json FROM Tili WHERE numero = ?", (account_number,))
            result = cursor.fetchone()
            
            if result:
                json_data = json.loads(result['json']) if result['json'] else {}
                
                # Update values
                json_data['nimi'] = name
                
                if vat_percent:
                    json_data['alvprosentti'] = float(vat_percent)
                elif 'alvprosentti' in json_data:
                    del json_data['alvprosentti']
                    
                if vat_code:
                    json_data['alvkoodi'] = int(vat_code)
                elif 'alvkoodi' in json_data:
                    del json_data['alvkoodi']
                
                # Update account
                cursor.execute("""
                    UPDATE Tili 
                    SET tyyppi = ?, json = ? 
                    WHERE numero = ?
                """, (account_type, json.dumps(json_data), account_number))
                
                conn.commit()
                flash(f"Account {account_number} updated successfully", "success")
            else:
                flash(f"Account {account_number} not found", "error")
        except Exception as e:
            conn.rollback()
            flash(f"Error updating account: {str(e)}", "error")
        
        return redirect(url_for('asetukset.chart_of_accounts', filename=filename))
    
    # GET request - display form with current account data
    cursor.execute("SELECT numero, tyyppi, json FROM Tili WHERE numero = ?", (account_number,))
    account = cursor.fetchone()
    
    if not account:
        conn.close()
        flash(f"Account {account_number} not found", "error")
        return redirect(url_for('asetukset.chart_of_accounts', filename=filename))
    
    json_data = json.loads(account['json']) if account['json'] else {}
    
    account_data = {
        'numero': account['numero'],
        'nimi': json_data.get('nimi', f"Account {account['numero']}"),
        'tyyppi': account['tyyppi'],
        'alvprosentti': json_data.get('alvprosentti', ''),
        'alvkoodi': json_data.get('alvkoodi', '')
    }
    
    conn.close()
    
    # Get client name
    client_name = ""
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT arvo FROM Asetus WHERE avain='Nimi'")
        result = cursor.fetchone()
        if result:
            client_name = result[0]
        conn.close()
    except:
        pass
    
    vat_codes = get_vat_codes()
    account_types = get_account_types()
    
    return render_template('settings/edit_account.html', 
                          account=account_data,
                          filename=filename,
                          client_name=client_name,
                          vat_codes=vat_codes,
                          account_types=account_types)

@asetukset_bp.route('/db/<filename>/settings/account/new', methods=['GET', 'POST'])
def new_account(filename):
    """Create a new account"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        # Get form data
        number = request.form.get('number', '')
        name = request.form.get('name', '')
        account_type = request.form.get('type', '')
        vat_percent = request.form.get('vat_percent', '')
        vat_code = request.form.get('vat_code', '')
        
        if not number or not name or not account_type:
            flash("Account number, name, and type are required", "error")
            return redirect(url_for('asetukset.new_account', filename=filename))
        
        try:
            # Create JSON data
            json_data = {'nimi': name}
            
            if vat_percent:
                json_data['alvprosentti'] = float(vat_percent)
                
            if vat_code:
                json_data['alvkoodi'] = int(vat_code)
            
            # Insert new account
            cursor.execute("""
                INSERT INTO Tili (numero, tyyppi, json)
                VALUES (?, ?, ?)
            """, (int(number), account_type, json.dumps(json_data)))
            
            conn.commit()
            flash(f"Account {number} created successfully", "success")
            return redirect(url_for('asetukset.chart_of_accounts', filename=filename))
        except Exception as e:
            conn.rollback()
            flash(f"Error creating account: {str(e)}", "error")
    
    conn.close()
    
    # Get client name
    client_name = ""
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT arvo FROM Asetus WHERE avain='Nimi'")
        result = cursor.fetchone()
        if result:
            client_name = result[0]
        conn.close()
    except:
        pass
    
    vat_codes = get_vat_codes()
    account_types = get_account_types()
    
    # Suggest next available account number
    next_account = 1000
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(numero) FROM Tili")
        result = cursor.fetchone()
        if result and result[0]:
            next_account = result[0] + 10
        conn.close()
    except:
        pass
    
    return render_template('settings/new_account.html', 
                          filename=filename,
                          client_name=client_name,
                          vat_codes=vat_codes,
                          account_types=account_types,
                          next_account=next_account)

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

def get_account_types():
    """Return a dictionary of account types"""
    return {
        'A': 'Asset',
        'B': 'Liability',
        'C': 'Equity',
        'D': 'Income',
        'E': 'Expense'
    }

def get_vat_codes():
    """Return a dictionary of VAT codes"""
    return {
        0: "Veroton / Ei alv-käsittelyä",  # No VAT
        1: "Yleinen ALV",                  # Standard VAT
        2: "Alennettu ALV 1",              # Reduced VAT 1
        3: "Alennettu ALV 2",              # Reduced VAT 2
        4: "Nollaverokannan alainen myynti",  # Zero-rated sales
        5: "Yhteisömyynti",                # EU sales
        6: "Yhteisöhankinnat",             # EU purchases
        7: "Rakennuspalvelu- tai käännetyn verovelvollisuuden alaiset ostot",  # Reverse charge
        8: "Rakennuspalvelu- tai käännetyn verovelvollisuuden alainen myynti",  # Reverse charge sales
        9: "Maahantuonti",                 # Imports
        10: "Marginaaliverotus",           # Margin scheme
        11: "Muu alv-käsittely",           # Other VAT treatment
        12: "Ei alv-vähennystä"            # No VAT deduction
    }

def register_blueprint(app):
    """Register the blueprint with the main Flask app"""
    app.register_blueprint(asetukset_bp) 