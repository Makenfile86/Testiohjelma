import os
import json
import sqlite3
import datetime
import hashlib
from pathlib import Path
from flask import Flask, Blueprint, render_template, request, redirect, url_for, flash, abort, g, jsonify, send_file, Response
from werkzeug.utils import secure_filename

# Create a Blueprint for invoice routes
lasku_bp = Blueprint('lasku', __name__, template_folder='templates')

# Invoice types (laskutyypit) based on Kitupiikki
INVOICE_TYPES = {
    1: "Myyntilasku",         # Sales invoice
    2: "Hyvityslasku",        # Credit note
    3: "Maksumuistutus",      # Payment reminder
    4: "Tarjous",             # Offer/Quote
    5: "Tilausvahvistus",     # Order confirmation
    6: "Lähetysluettelo",     # Delivery note
    7: "Koontilasku",         # Collective invoice
    8: "Ennakkolasku",        # Advance invoice
    9: "Suoraveloitus",       # Direct debit
    10: "Toistuva lasku"      # Recurring invoice
}

# Invoice statuses (laskun tilat)
INVOICE_STATUSES = {
    0: "Luonnos",           # Draft
    1: "Avoin",             # Open
    2: "Erääntynyt",        # Overdue
    3: "Osittain maksettu", # Partially paid
    4: "Maksettu",          # Paid
    5: "Hyvitetty",         # Credited
    6: "Mitätöity"          # Voided
}

# Payment methods (maksutavat)
PAYMENT_METHODS = {
    1: "Tilisiirto",        # Bank transfer
    2: "Käteinen",          # Cash
    3: "Korttimaksu",       # Card payment
    4: "Verkkomaksu",       # Online payment
    5: "Muu maksutapa"      # Other payment method
}

# Allowed file extensions for attachments
ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png', 'csv', 'txt', 'xls', 'xlsx', 'doc', 'docx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

def get_db_connection(db_path):
    """Create a connection to the specified database file"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name
    return conn

def get_accounts(db_path):
    """Get all accounts from database"""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT numero, json FROM Tili
        ORDER BY numero
    """)
    
    accounts = []
    for row in cursor.fetchall():
        json_data = json.loads(row['json']) if row['json'] else {}
        accounts.append({
            'numero': row['numero'],
            'nimi': json_data.get('nimi', f"Tili {row['numero']}")
        })
    
    conn.close()
    return accounts

def get_partners(db_path):
    """Get all partners from database"""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, nimi FROM Kumppani
        ORDER BY nimi
    """)
    
    partners = []
    for row in cursor.fetchall():
        partners.append({
            'id': row['id'],
            'nimi': row['nimi']
        })
    
    conn.close()
    return partners

def get_partner_details(db_path, partner_id):
    """Get detailed information about a partner"""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, nimi, alvtunnus, json FROM Kumppani
        WHERE id = ?
    """, (partner_id,))
    
    partner = cursor.fetchone()
    
    if partner and partner['json']:
        partner_json = json.loads(partner['json'])
    else:
        partner_json = {}
    
    conn.close()
    
    return partner, partner_json

def get_allocations(db_path):
    """Get all allocations from database"""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, json FROM Kohdennus
        ORDER BY id
    """)
    
    allocations = []
    for row in cursor.fetchall():
        json_data = json.loads(row['json']) if row['json'] else {}
        name = json_data.get('nimi', {}).get('fi', f"Kohdennus {row['id']}")
        allocations.append({
            'id': row['id'],
            'nimi': name
        })
    
    conn.close()
    return allocations

def generate_invoice_number(db_path):
    """Generate a sequential invoice number"""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get the highest invoice number
    cursor.execute("""
        SELECT MAX(tunniste) FROM Tosite 
        WHERE tyyppi=1
    """)
    
    result = cursor.fetchone()
    highest_number = result[0] if result[0] else 0
    
    conn.close()
    
    # Return the next number
    return highest_number + 1

def allowed_file(filename):
    """Check if a file has an allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_file_hash(file_data):
    """Generate SHA-256 hash for a file"""
    return hashlib.sha256(file_data).hexdigest()

@lasku_bp.route('/db/<filename>/invoices')
def list_invoices(filename):
    """List all invoices in a database"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Tietokantaa ei löydy", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get all invoices (vouchers with type 1 = Myyntilasku)
    cursor.execute("""
        SELECT id, pvm, tunniste, tila, otsikko, erapvm, kumppani, json FROM Tosite
        WHERE tyyppi = 1
        ORDER BY pvm DESC, id DESC
    """)
    
    invoices = []
    for row in cursor.fetchall():
        # Get the status
        status = INVOICE_STATUSES.get(row['tila'], "Tuntematon")
        
        # Get partner name
        partner_name = "Tuntematon"
        if row['kumppani']:
            cursor.execute("SELECT nimi FROM Kumppani WHERE id = ?", (row['kumppani'],))
            partner = cursor.fetchone()
            if partner:
                partner_name = partner['nimi']
        
        # Get total amount from transactions
        cursor.execute("""
            SELECT SUM(kreditsnt) as total_credit FROM Vienti
            WHERE tosite = ?
        """, (row['id'],))
        
        total = cursor.fetchone()
        total_amount = float(total['total_credit'] / 100) if total['total_credit'] else 0
        
        # Get JSON data
        json_data = {}
        if row['json']:
            try:
                json_data = json.loads(row['json'])
            except:
                pass
        
        invoices.append({
            'id': row['id'],
            'number': row['tunniste'] or row['id'],  # Use tunniste if available, otherwise id
            'pvm': row['pvm'],
            'tila': status,
            'otsikko': row['otsikko'] or "Ei otsikkoa",
            'erapvm': row['erapvm'],
            'kumppani': partner_name,
            'summa': f"{total_amount:.2f} €",
            'maksuehto': json_data.get('maksuehto', ""),
            'viitenumero': json_data.get('viitenumero', "")
        })
    
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
    
    return render_template('invoices/list.html', 
                          invoices=invoices, 
                          filename=filename, 
                          client_name=client_name)

@lasku_bp.route('/db/<filename>/invoices/new', methods=['GET', 'POST'])
def new_invoice(filename):
    """Create a new invoice"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Tietokantaa ei löydy", "error")
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        # Get the partner
        partner_id = request.form.get('kumppani')
        if not partner_id or not partner_id.strip():
            flash("Lasku tarvitsee asiakkaan", "error")
            return redirect(url_for('lasku.new_invoice', filename=filename))
        
        partner_id = int(partner_id)
        
        # Get form data
        invoice_date = request.form.get('pvm', datetime.date.today().isoformat())
        title = request.form.get('otsikko', '')
        due_date = request.form.get('erapvm')
        reference = request.form.get('viite', '')
        payment_terms = request.form.get('maksuehto', '14 päivää')
        status = int(request.form.get('tila', 0))  # Default to Draft (0)
        payment_method = int(request.form.get('maksutapa', 1))  # Default to Bank transfer (1)
        
        # Calculate due date if not provided
        if not due_date:
            # Parse payment terms to get number of days
            try:
                days = int(payment_terms.split()[0])
                due_date = (datetime.datetime.strptime(invoice_date, '%Y-%m-%d') + 
                           datetime.timedelta(days=days)).strftime('%Y-%m-%d')
            except:
                due_date = (datetime.datetime.strptime(invoice_date, '%Y-%m-%d') + 
                           datetime.timedelta(days=14)).strftime('%Y-%m-%d')
        
        # Generate invoice number
        invoice_number = generate_invoice_number(db_path)
        
        # JSON data for additional fields
        json_data = {
            'maksuehto': payment_terms,
            'maksutapa': payment_method,
            'viitenumero': reference
        }
        
        # Additional fields
        comments = request.form.get('comments')
        if comments:
            json_data['kommentti'] = comments
        
        # Create voucher (invoice) in database
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        
        try:
            # Begin transaction
            conn.execute('BEGIN')
            
            # Insert voucher with type 1 (Myyntilasku)
            cursor.execute("""
                INSERT INTO Tosite (pvm, tyyppi, tila, otsikko, kumppani, tunniste, erapvm, viite, json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (invoice_date, 1, status, title, partner_id, invoice_number, due_date, 
                  reference, json.dumps(json_data) if json_data else None))
            
            invoice_id = cursor.lastrowid
            
            # Process line items
            row_index = 1
            total_amount = 0
            for i in range(50):  # Arbitrary limit for form input fields
                product_key = f'tuote_{i}'
                if product_key not in request.form or not request.form.get(product_key):
                    continue  # Skip if no product provided
                
                product = request.form.get(product_key)
                quantity = float(request.form.get(f'maara_{i}', 1))
                price = float(request.form.get(f'hinta_{i}', 0))
                discount = float(request.form.get(f'alennus_{i}', 0))
                vat_percent = float(request.form.get(f'alv_percent_{i}', 24))
                account = int(request.form.get(f'tili_{i}', 3000))  # Default to sales account
                
                # Calculate line amount
                amount = quantity * price
                
                # Apply discount
                if discount > 0:
                    amount = amount * (1 - discount / 100)
                
                # Calculate VAT
                vat_amount = amount * (vat_percent / 100)
                total_line_amount = amount + vat_amount
                total_amount += total_line_amount
                
                # Convert to cents
                amount_cents = int(amount * 100)
                vat_amount_cents = int(vat_amount * 100)
                
                # Get allocation if provided
                allocation = request.form.get(f'kohdennus_{i}')
                if allocation and allocation.strip():
                    allocation = int(allocation)
                else:
                    allocation = 0  # Default allocation
                
                # Description for transaction
                description = f"{product} {quantity} x {price:.2f}€"
                if discount > 0:
                    description += f" (Alennus: {discount}%)"
                
                # Insert transaction for the amount excluding VAT
                cursor.execute("""
                    INSERT INTO Vienti (rivi, tosite, pvm, tili, kohdennus, selite, 
                                      kreditsnt, alvprosentti)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (row_index, invoice_id, invoice_date, account, allocation, description,
                      amount_cents, vat_percent))
                
                row_index += 1
                
                # Insert transaction for VAT
                if vat_percent > 0:
                    cursor.execute("""
                        INSERT INTO Vienti (rivi, tosite, pvm, tili, kohdennus, selite, 
                                         kreditsnt, alvprosentti)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (row_index, invoice_id, invoice_date, 2940, allocation, f"ALV {vat_percent}%: {description}",
                         vat_amount_cents, 0))
                    
                    row_index += 1
            
            # Transaction for accounts receivable
            cursor.execute("""
                INSERT INTO Vienti (rivi, tosite, pvm, tili, selite, debetsnt)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (row_index, invoice_id, invoice_date, 1700, "Myyntisaamiset", int(total_amount * 100)))
            
            # Handle file attachments
            if 'attachment' in request.files:
                files = request.files.getlist('attachment')
                
                for i, file in enumerate(files):
                    if file and file.filename and allowed_file(file.filename):
                        filename_secure = secure_filename(file.filename)
                        file_data = file.read()
                        
                        # Check file size
                        if len(file_data) > MAX_FILE_SIZE:
                            flash(f"Tiedosto {filename_secure} on liian suuri (max 10MB sallittu)", "error")
                            continue
                        
                        # Determine file type
                        file_type = file.content_type or 'application/octet-stream'
                        
                        # Generate hash
                        file_hash = generate_file_hash(file_data)
                        
                        # Role name - 'original' for first attachment, numbered for others
                        role_name = "original" if i == 0 else f"attachment{i}"
                        
                        # Store in database
                        cursor.execute("""
                            INSERT INTO Liite (tosite, nimi, roolinimi, tyyppi, sha, data)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (invoice_id, filename_secure, role_name, file_type, file_hash, file_data))
            
            # Commit transaction
            conn.commit()
            flash(f"Lasku luotu onnistuneesti numerolla: {invoice_number}", "success")
            return redirect(url_for('lasku.view_invoice', filename=filename, invoice_id=invoice_id))
        
        except Exception as e:
            conn.rollback()
            flash(f"Virhe laskun luonnissa: {str(e)}", "error")
        finally:
            conn.close()
    
    # Get data for form fields
    accounts = get_accounts(db_path)
    partners = get_partners(db_path)
    allocations = get_allocations(db_path)
    
    # Get default sales accounts
    default_accounts = []
    for account in accounts:
        if 3000 <= account['numero'] < 4000:  # Sales accounts typically start with 3
            default_accounts.append(account)
    
    if not default_accounts and accounts:
        default_accounts = [accounts[0]]  # Use first account if no sales accounts found
    
    return render_template('invoices/new.html',
                          filename=filename,
                          invoice_statuses=INVOICE_STATUSES,
                          payment_methods=PAYMENT_METHODS,
                          accounts=accounts,
                          default_accounts=default_accounts,
                          partners=partners,
                          allocations=allocations,
                          today=datetime.date.today().isoformat())

@lasku_bp.route('/db/<filename>/invoices/<int:invoice_id>')
def view_invoice(filename, invoice_id):
    """View a specific invoice"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Tietokantaa ei löydy", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get invoice details (as a voucher with type 1 = Myyntilasku)
    cursor.execute("""
        SELECT id, pvm, tunniste, tila, otsikko, kumppani, laskupvm, erapvm, viite, json
        FROM Tosite
        WHERE id = ? AND tyyppi = 1
    """, (invoice_id,))
    
    invoice = cursor.fetchone()
    
    if not invoice:
        conn.close()
        flash("Laskua ei löydy", "error")
        return redirect(url_for('lasku.list_invoices', filename=filename))
    
    # Parse JSON data
    json_data = {}
    if invoice['json']:
        try:
            json_data = json.loads(invoice['json'])
        except:
            pass
    
    # Get payment method
    payment_method = PAYMENT_METHODS.get(json_data.get('maksutapa', 1), "Tilisiirto")
    
    # Get partner details if exists
    partner = None
    partner_json = {}
    if invoice['kumppani']:
        partner, partner_json = get_partner_details(db_path, invoice['kumppani'])
    
    # Get transactions
    cursor.execute("""
        SELECT id, rivi, tili, kohdennus, selite, debetsnt, kreditsnt, alvprosentti, alvkoodi
        FROM Vienti
        WHERE tosite = ?
        ORDER BY rivi
    """, (invoice_id,))
    
    transactions = []
    invoice_lines = []
    total_amount = 0
    total_vat = 0
    
    for row in cursor.fetchall():
        debit_amount = row['debetsnt'] / 100 if row['debetsnt'] else 0
        credit_amount = row['kreditsnt'] / 100 if row['kreditsnt'] else 0
        
        # Get account details
        account_name = ""
        cursor.execute("SELECT json FROM Tili WHERE numero = ?", (row['tili'],))
        account_result = cursor.fetchone()
        if account_result and account_result['json']:
            try:
                account_data = json.loads(account_result['json'])
                account_name = account_data.get('nimi', '')
            except:
                pass
        
        # Add to transactions
        transactions.append({
            'id': row['id'],
            'rivi': row['rivi'],
            'tili': row['tili'],
            'tili_nimi': account_name,
            'selite': row['selite'],
            'debit': debit_amount,
            'credit': credit_amount,
            'alv_percent': row['alvprosentti']
        })
        
        # Process for invoice lines
        if credit_amount > 0 and row['tili'] >= 3000 and row['tili'] < 4000:
            # This is a revenue account, so it's an invoice line
            invoice_lines.append({
                'selite': row['selite'],
                'summa': credit_amount,
                'alv': row['alvprosentti']
            })
            total_amount += credit_amount
        
        # Sum up VAT amounts
        if credit_amount > 0 and row['tili'] >= 2900 and row['tili'] < 3000:
            # This is a VAT account
            total_vat += credit_amount
    
    # Get attachments
    cursor.execute("""
        SELECT id, nimi, roolinimi, tyyppi
        FROM Liite
        WHERE tosite = ?
        ORDER BY roolinimi
    """, (invoice_id,))
    
    attachments = []
    for row in cursor.fetchall():
        attachments.append({
            'id': row['id'],
            'nimi': row['nimi'],
            'roolinimi': row['roolinimi'],
            'tyyppi': row['tyyppi']
        })
    
    # Get client information
    client_info = {}
    cursor.execute("SELECT avain, arvo FROM Asetus")
    for row in cursor.fetchall():
        client_info[row['avain']] = row['arvo']
    
    conn.close()
    
    return render_template('invoices/view.html',
                          filename=filename,
                          invoice=invoice,
                          invoice_number=invoice['tunniste'] or invoice['id'],
                          json_data=json_data,
                          partner=partner,
                          partner_json=partner_json,
                          transactions=transactions,
                          invoice_lines=invoice_lines,
                          status=INVOICE_STATUSES.get(invoice['tila'], "Tuntematon"),
                          payment_method=payment_method,
                          total_amount=total_amount,
                          total_vat=total_vat,
                          total_with_vat=total_amount+total_vat,
                          client_info=client_info,
                          attachments=attachments)

@lasku_bp.route('/db/<filename>/invoices/<int:invoice_id>/attachment/<int:attachment_id>')
def view_attachment(filename, invoice_id, attachment_id):
    """View an invoice attachment"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Tietokantaa ei löydy", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get attachment details
    cursor.execute("""
        SELECT nimi, tyyppi, data 
        FROM Liite 
        WHERE id = ? AND tosite = ?
    """, (attachment_id, invoice_id))
    
    attachment = cursor.fetchone()
    
    if not attachment:
        conn.close()
        flash("Liitettä ei löydy", "error")
        return redirect(url_for('lasku.view_invoice', filename=filename, invoice_id=invoice_id))
    
    file_data = attachment['data']
    file_type = attachment['tyyppi']
    file_name = attachment['nimi']
    
    conn.close()
    
    # Send the file to the client
    return Response(
        file_data,
        mimetype=file_type,
        headers={"Content-Disposition": f"inline; filename={file_name}"}
    )

@lasku_bp.route('/db/<filename>/invoices/<int:invoice_id>/delete', methods=['POST'])
def delete_invoice(filename, invoice_id):
    """Delete an invoice"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Tietokantaa ei löydy", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    
    try:
        # Verify this is an invoice
        cursor = conn.cursor()
        cursor.execute("SELECT tyyppi FROM Tosite WHERE id = ?", (invoice_id,))
        result = cursor.fetchone()
        
        if not result or result['tyyppi'] != 1:
            conn.close()
            flash("Laskua ei löydy", "error")
            return redirect(url_for('lasku.list_invoices', filename=filename))
        
        # Delete invoice - transactions will be deleted automatically due to CASCADE constraint
        conn.execute("DELETE FROM Tosite WHERE id = ?", (invoice_id,))
        conn.commit()
        flash("Lasku poistettu onnistuneesti", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Virhe laskun poistamisessa: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('lasku.list_invoices', filename=filename))

@lasku_bp.route('/db/<filename>/invoices/<int:invoice_id>/mark-paid', methods=['POST'])
def mark_invoice_paid(filename, invoice_id):
    """Mark an invoice as paid"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Tietokantaa ei löydy", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        # Verify this is an invoice
        cursor.execute("SELECT tyyppi, tila FROM Tosite WHERE id = ?", (invoice_id,))
        result = cursor.fetchone()
        
        if not result or result['tyyppi'] != 1:
            conn.close()
            flash("Laskua ei löydy", "error")
            return redirect(url_for('lasku.list_invoices', filename=filename))
        
        # Check if already paid
        if result['tila'] == 4:
            conn.close()
            flash("Lasku on jo merkitty maksetuksi", "warning")
            return redirect(url_for('lasku.view_invoice', filename=filename, invoice_id=invoice_id))
        
        # Begin transaction
        conn.execute('BEGIN')
        
        # Update invoice status to Paid (4)
        conn.execute("UPDATE Tosite SET tila = 4 WHERE id = ?", (invoice_id,))
        
        # Get payment details from form
        payment_date = request.form.get('payment_date', datetime.date.today().isoformat())
        payment_amount = request.form.get('payment_amount')
        payment_account = request.form.get('payment_account', '1910')  # Default bank account
        
        # Create payment voucher
        cursor.execute("""
            INSERT INTO Tosite (pvm, tyyppi, tila, otsikko)
            VALUES (?, ?, ?, ?)
        """, (payment_date, 5, 100, f"Maksusuoritus laskulle #{invoice_id}"))
        
        payment_id = cursor.lastrowid
        
        # Get total amount if not provided
        if not payment_amount:
            cursor.execute("""
                SELECT SUM(debetsnt) as total_debit FROM Vienti
                WHERE tosite = ? AND tili = 1700
            """, (invoice_id,))
            total = cursor.fetchone()
            payment_amount = total['total_debit'] / 100 if total['total_debit'] else 0
        else:
            payment_amount = float(payment_amount)
        
        # Create transactions for payment voucher
        # Debit bank account
        cursor.execute("""
            INSERT INTO Vienti (rivi, tosite, pvm, tili, selite, debetsnt)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (1, payment_id, payment_date, int(payment_account), 
              f"Maksusuoritus laskulle #{invoice_id}", int(payment_amount * 100)))
        
        # Credit accounts receivable
        cursor.execute("""
            INSERT INTO Vienti (rivi, tosite, pvm, tili, selite, kreditsnt, eraid)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (2, payment_id, payment_date, 1700, 
              f"Maksusuoritus laskulle #{invoice_id}", int(payment_amount * 100), invoice_id))
        
        # Link the two vouchers
        payment_json = {"linked_invoice": invoice_id}
        conn.execute("UPDATE Tosite SET json = ? WHERE id = ?", 
                    (json.dumps(payment_json), payment_id))
        
        # Get current invoice JSON
        cursor.execute("SELECT json FROM Tosite WHERE id = ?", (invoice_id,))
        current_json = cursor.fetchone()
        
        if current_json and current_json['json']:
            try:
                invoice_json = json.loads(current_json['json'])
                invoice_json["payment_voucher"] = payment_id
                invoice_json["payment_date"] = payment_date
                
                conn.execute("UPDATE Tosite SET json = ? WHERE id = ?", 
                            (json.dumps(invoice_json), invoice_id))
            except:
                pass
        else:
            invoice_json = {"payment_voucher": payment_id, "payment_date": payment_date}
            conn.execute("UPDATE Tosite SET json = ? WHERE id = ?", 
                        (json.dumps(invoice_json), invoice_id))
        
        # Commit transaction
        conn.commit()
        flash("Lasku merkitty maksetuksi ja maksusuoritus kirjattu", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Virhe laskun maksetuksi merkitsemisessä: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('lasku.view_invoice', filename=filename, invoice_id=invoice_id))

@lasku_bp.route('/db/<filename>/invoices/<int:invoice_id>/print')
def print_invoice(filename, invoice_id):
    """Generate a printable invoice"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Tietokantaa ei löydy", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get invoice details
    cursor.execute("""
        SELECT id, pvm, tunniste, tila, otsikko, kumppani, laskupvm, erapvm, viite, json
        FROM Tosite
        WHERE id = ? AND tyyppi = 1
    """, (invoice_id,))
    
    invoice = cursor.fetchone()
    
    if not invoice:
        conn.close()
        flash("Laskua ei löydy", "error")
        return redirect(url_for('lasku.list_invoices', filename=filename))
    
    # Parse JSON data
    json_data = {}
    if invoice['json']:
        try:
            json_data = json.loads(invoice['json'])
        except:
            pass
    
    # Get partner details
    partner = None
    partner_json = {}
    if invoice['kumppani']:
        partner, partner_json = get_partner_details(db_path, invoice['kumppani'])
    
    # Get transactions (invoice lines)
    cursor.execute("""
        SELECT tili, selite, kreditsnt, alvprosentti
        FROM Vienti
        WHERE tosite = ? AND kreditsnt > 0 AND tili BETWEEN 3000 AND 3999
        ORDER BY rivi
    """, (invoice_id,))
    
    invoice_lines = []
    subtotal = 0
    
    for row in cursor.fetchall():
        amount = row['kreditsnt'] / 100
        subtotal += amount
        
        invoice_lines.append({
            'selite': row['selite'],
            'summa': amount,
            'alv_percent': row['alvprosentti'],
            'alv_maara': amount * (row['alvprosentti'] / 100) if row['alvprosentti'] else 0
        })
    
    # Get VAT totals
    cursor.execute("""
        SELECT SUM(kreditsnt) as vat_total
        FROM Vienti
        WHERE tosite = ? AND kreditsnt > 0 AND tili BETWEEN 2900 AND 2999
    """, (invoice_id,))
    
    vat_result = cursor.fetchone()
    vat_total = vat_result['vat_total'] / 100 if vat_result['vat_total'] else 0
    
    # Get company details
    client_info = {}
    cursor.execute("SELECT avain, arvo FROM Asetus")
    for row in cursor.fetchall():
        client_info[row['avain']] = row['arvo']
    
    conn.close()
    
    # Render the invoice template
    return render_template('invoices/print.html',
                          filename=filename,
                          invoice=invoice,
                          invoice_number=invoice['tunniste'] or invoice['id'],
                          json_data=json_data,
                          partner=partner,
                          partner_json=partner_json,
                          invoice_lines=invoice_lines,
                          subtotal=subtotal,
                          vat_total=vat_total,
                          total=subtotal + vat_total,
                          status=INVOICE_STATUSES.get(invoice['tila'], "Tuntematon"),
                          client_info=client_info)

def register_blueprint(app):
    """Register the blueprint with the main Flask app"""
    app.register_blueprint(lasku_bp) 