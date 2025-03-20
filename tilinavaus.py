import os
import json
import sqlite3
import datetime
from pathlib import Path
from flask import Flask, Blueprint, render_template, request, redirect, url_for, flash, abort, g, jsonify

# Create a Blueprint for opening balance routes
tilinavaus_bp = Blueprint('tilinavaus', __name__, template_folder='templates')

def get_db_connection(db_path):
    """Create a connection to the specified database file"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name
    return conn

@tilinavaus_bp.route('/db/<filename>/opening_balances', methods=['GET', 'POST'])
def manage_opening_balances(filename):
    """View and manage opening balances for accounts"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Check if there's an existing opening balance voucher
    opening_balance_exists = False
    opening_balance_id = None
    
    try:
        cursor.execute("""
            SELECT id FROM Tosite 
            WHERE tyyppi = 10 AND otsikko LIKE 'Opening Balances%'
            ORDER BY id DESC LIMIT 1
        """)
        result = cursor.fetchone()
        if result:
            opening_balance_id = result['id']
            opening_balance_exists = True
    except:
        pass
    
    # Handle form submission (POST)
    if request.method == 'POST':
        date = request.form.get('date', datetime.date.today().isoformat())
        accounts = []
        
        # Get all account numbers and their balances from the form
        debit_total = 0
        credit_total = 0
        
        for key, value in request.form.items():
            if key.startswith('debit_') and value.strip():
                account_number = int(key.replace('debit_', ''))
                debit_amount = float(value)
                debit_total += debit_amount
                accounts.append({
                    'account': account_number,
                    'debit': debit_amount,
                    'credit': 0
                })
            elif key.startswith('credit_') and value.strip():
                account_number = int(key.replace('credit_', ''))
                credit_amount = float(value)
                credit_total += credit_amount
                accounts.append({
                    'account': account_number,
                    'debit': 0,
                    'credit': credit_amount
                })
        
        # Validate that debits = credits (accounting equation)
        if abs(debit_total - credit_total) > 0.01:
            flash(f"Error: Debits ({debit_total:.2f}) must equal credits ({credit_total:.2f})", "error")
        else:
            # If an opening balance already exists, delete it first
            if opening_balance_exists and opening_balance_id:
                try:
                    cursor.execute("DELETE FROM Tosite WHERE id = ?", (opening_balance_id,))
                    # Transactions are automatically deleted due to CASCADE constraint
                except Exception as e:
                    conn.rollback()
                    flash(f"Error deleting existing opening balances: {str(e)}", "error")
                    return redirect(url_for('tilinavaus.manage_opening_balances', filename=filename))
            
            # Create a new opening balance voucher
            try:
                # Begin transaction
                conn.execute('BEGIN')
                
                # Insert voucher
                cursor.execute("""
                    INSERT INTO Tosite (pvm, tyyppi, tila, otsikko)
                    VALUES (?, ?, ?, ?)
                """, (date, 10, 100, f"Opening Balances as of {date}"))
                
                voucher_id = cursor.lastrowid
                
                # Insert transactions for each account with a balance
                row_index = 1
                for account in accounts:
                    if account['debit'] > 0 or account['credit'] > 0:
                        # Convert to cents
                        debit_cents = int(account['debit'] * 100) if account['debit'] > 0 else 0
                        credit_cents = int(account['credit'] * 100) if account['credit'] > 0 else 0
                        
                        cursor.execute("""
                            INSERT INTO Vienti (rivi, tosite, pvm, tili, selite, debetsnt, kreditsnt)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            row_index, 
                            voucher_id, 
                            date, 
                            account['account'], 
                            "Opening Balance", 
                            debit_cents, 
                            credit_cents
                        ))
                        row_index += 1
                
                # Commit transaction
                conn.commit()
                flash("Opening balances saved successfully", "success")
                return redirect(url_for('tili.list_balances', filename=filename))
            
            except Exception as e:
                conn.rollback()
                flash(f"Error saving opening balances: {str(e)}", "error")
    
    # GET request - display form with accounts and existing balances
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
            'debit': 0,
            'credit': 0
        }
        accounts.append(account)
    
    # If there's an existing opening balance, get the current values
    if opening_balance_exists and opening_balance_id:
        cursor.execute("""
            SELECT tili, debetsnt, kreditsnt 
            FROM Vienti 
            WHERE tosite = ?
        """, (opening_balance_id,))
        
        for row in cursor.fetchall():
            account_number = row['tili']
            debit_cents = row['debetsnt'] or 0
            credit_cents = row['kreditsnt'] or 0
            
            # Update the corresponding account in our list
            for account in accounts:
                if account['numero'] == account_number:
                    account['debit'] = debit_cents / 100
                    account['credit'] = credit_cents / 100
                    break
    
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
    
    conn.close()
    
    # Get today's date for the form
    today = datetime.date.today().isoformat()
    
    # If we have an opening balance, get its date
    opening_balance_date = today
    if opening_balance_exists and opening_balance_id:
        try:
            conn = get_db_connection(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT pvm FROM Tosite WHERE id = ?", (opening_balance_id,))
            result = cursor.fetchone()
            if result and result['pvm']:
                opening_balance_date = result['pvm']
            conn.close()
        except:
            pass
    
    return render_template('accounts/opening_balances.html', 
                          grouped_accounts=grouped_accounts,
                          filename=filename,
                          client_name=client_name,
                          today=today,
                          opening_balance_date=opening_balance_date,
                          opening_balance_exists=opening_balance_exists)

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

def register_blueprint(app):
    """Register the blueprint with the main Flask app"""
    app.register_blueprint(tilinavaus_bp) 