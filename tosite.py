import os
import json
import sqlite3
import datetime
import hashlib
from pathlib import Path
from flask import Flask, Blueprint, render_template, request, redirect, url_for, flash, abort, g, jsonify, send_file, Response
from werkzeug.utils import secure_filename

# Create a Blueprint for voucher routes
tosite_bp = Blueprint('tosite', __name__, template_folder='templates')

# Voucher types (tositetyyppi) based on Kitsas
VOUCHER_TYPES = {
    1: "Myyntilasku",  # Sales invoice
    2: "Ostolasku",    # Purchase invoice
    3: "Tulo",         # Income
    4: "Meno",         # Expense
    5: "Siirto",       # Transfer
    6: "Tiliote",      # Bank statement
    7: "Muistio",      # Memo
    8: "Liitetieto",   # Attachment
    9: "Alv",          # VAT
    10: "Muistiotosite"  # Journal entry
}

# VAT codes (alv codes) based on Kitsas
VAT_CODES = {
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

# Statuses for vouchers
VOUCHER_STATUSES = {
    0: "Luonnos",     # Draft
    100: "Valmis",    # Ready
    200: "Kirjattu",  # Posted
    300: "Arkistoitu" # Archived
}

# Allowed file extensions for attachments
ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png', 'csv', 'txt', 'xls', 'xlsx', 'doc', 'docx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

def get_db_connection(db_path):
    """Create a connection to the specified database file"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name
    return conn

def get_client_databases():
    """Get list of all client databases"""
    databases = []
    db_dir = Path("databases")
    
    if db_dir.exists():
        for file in db_dir.glob("*.db"):
            # Get creation time
            created = datetime.datetime.fromtimestamp(file.stat().st_ctime)
            # Get client name from filename
            name = file.stem.split('_')[0].replace('_', ' ').title()
            
            # Try to get actual name from database
            try:
                conn = get_db_connection(file)
                cursor = conn.cursor()
                cursor.execute("SELECT arvo FROM Asetus WHERE avain='Nimi'")
                result = cursor.fetchone()
                if result:
                    name = result[0]
                conn.close()
            except:
                pass
                
            databases.append({
                'filename': file.name,
                'name': name,
                'created': created.strftime('%Y-%m-%d %H:%M'),
                'size': f"{file.stat().st_size / 1024:.1f} KB"
            })
    
    return databases

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
            'nimi': json_data.get('nimi', f"Account {row['numero']}")
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
        name = json_data.get('nimi', {}).get('fi', f"Allocation {row['id']}")
        allocations.append({
            'id': row['id'],
            'nimi': name
        })
    
    conn.close()
    return allocations

def allowed_file(filename):
    """Check if a file has an allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_file_hash(file_data):
    """Generate SHA-256 hash for a file"""
    return hashlib.sha256(file_data).hexdigest()

@tosite_bp.route('/db/<filename>/vouchers')
def list_vouchers(filename):
    """List all vouchers in a database"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get all vouchers
    cursor.execute("""
        SELECT id, pvm, tyyppi, tila, otsikko FROM Tosite
        ORDER BY pvm DESC, id DESC
    """)
    
    vouchers = []
    for row in cursor.fetchall():
        voucher_type = VOUCHER_TYPES.get(row['tyyppi'], "Muu")
        status = VOUCHER_STATUSES.get(row['tila'], "Tuntematon")
        
        vouchers.append({
            'id': row['id'],
            'pvm': row['pvm'],
            'tyyppi': voucher_type,
            'tila': status,
            'otsikko': row['otsikko'] or "Untitled"
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
    
    return render_template('vouchers/list.html', 
                          vouchers=vouchers, 
                          filename=filename, 
                          client_name=client_name)

@tosite_bp.route('/db/<filename>/vouchers/new', methods=['GET', 'POST'])
def new_voucher(filename):
    """Create a new voucher"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        # Get form data
        voucher_type = int(request.form.get('tyyppi', 7))  # Default to Memo (7)
        voucher_date = request.form.get('pvm', datetime.date.today().isoformat())
        title = request.form.get('otsikko', '')
        partner_id = request.form.get('kumppani')
        if partner_id and partner_id.strip():
            partner_id = int(partner_id)
        else:
            partner_id = None
        
        invoice_date = request.form.get('laskupvm')
        due_date = request.form.get('erapvm')
        reference = request.form.get('viite', '')
        status = int(request.form.get('tila', 0))  # Default to Draft (0)
        
        # JSON data for additional fields
        json_data = {}
        comments = request.form.get('comments')
        if comments:
            json_data['kommentti'] = comments
        
        # Create voucher in database
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        
        try:
            # Begin transaction
            conn.execute('BEGIN')
            
            # Insert voucher
            cursor.execute("""
                INSERT INTO Tosite (pvm, tyyppi, tila, otsikko, kumppani, laskupvm, erapvm, viite, json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (voucher_date, voucher_type, status, title, partner_id, invoice_date, due_date, 
                  reference, json.dumps(json_data) if json_data else None))
            
            voucher_id = cursor.lastrowid
            
            # Process line items (transactions)
            row_index = 1
            for i in range(50):  # Arbitrary limit for form input fields
                account_key = f'tili_{i}'
                if account_key not in request.form or not request.form.get(account_key):
                    continue  # Skip if no account selected
                
                account = int(request.form.get(account_key))
                explanation = request.form.get(f'selite_{i}', '')
                debit = request.form.get(f'debit_{i}', '')
                credit = request.form.get(f'credit_{i}', '')
                
                # Convert amount to cents
                debit_cents = int(float(debit) * 100) if debit and debit.strip() else 0
                credit_cents = int(float(credit) * 100) if credit and credit.strip() else 0
                
                # Get allocation if provided
                allocation = request.form.get(f'kohdennus_{i}')
                if allocation and allocation.strip():
                    allocation = int(allocation)
                else:
                    allocation = 0  # Default allocation
                
                # Get VAT if provided
                vat_percent = request.form.get(f'alv_percent_{i}')
                vat_code = request.form.get(f'alv_code_{i}')
                
                if vat_percent and vat_percent.strip():
                    vat_percent = float(vat_percent)
                else:
                    vat_percent = None
                
                if vat_code and vat_code.strip():
                    vat_code = int(vat_code)
                else:
                    vat_code = None
                
                # Insert transaction
                cursor.execute("""
                    INSERT INTO Vienti (rivi, tosite, pvm, tili, kohdennus, selite, 
                                      debetsnt, kreditsnt, alvprosentti, alvkoodi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (row_index, voucher_id, voucher_date, account, allocation, explanation,
                      debit_cents, credit_cents, vat_percent, vat_code))
                
                row_index += 1
            
            # Handle file attachments
            if 'attachment' in request.files:
                files = request.files.getlist('attachment')
                
                for i, file in enumerate(files):
                    if file and file.filename and allowed_file(file.filename):
                        filename_secure = secure_filename(file.filename)
                        file_data = file.read()
                        
                        # Check file size
                        if len(file_data) > MAX_FILE_SIZE:
                            flash(f"File {filename_secure} is too large (max 10MB allowed)", "error")
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
                        """, (voucher_id, filename_secure, role_name, file_type, file_hash, file_data))
            
            # Commit transaction
            conn.commit()
            flash(f"Voucher created successfully with ID: {voucher_id}", "success")
            return redirect(url_for('tosite.view_voucher', filename=filename, voucher_id=voucher_id))
        
        except Exception as e:
            conn.rollback()
            flash(f"Error creating voucher: {str(e)}", "error")
        finally:
            conn.close()
    
    # Get data for form fields
    accounts = get_accounts(db_path)
    partners = get_partners(db_path)
    allocations = get_allocations(db_path)
    
    return render_template('vouchers/new.html',
                          filename=filename,
                          voucher_types=VOUCHER_TYPES,
                          voucher_statuses=VOUCHER_STATUSES,
                          vat_codes=VAT_CODES,
                          accounts=accounts,
                          partners=partners,
                          allocations=allocations,
                          today=datetime.date.today().isoformat())

@tosite_bp.route('/db/<filename>/vouchers/<int:voucher_id>')
def view_voucher(filename, voucher_id):
    """View a specific voucher"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get voucher details
    cursor.execute("""
        SELECT id, pvm, tyyppi, tila, otsikko, kumppani, laskupvm, erapvm, viite, json
        FROM Tosite
        WHERE id = ?
    """, (voucher_id,))
    
    voucher = cursor.fetchone()
    
    if not voucher:
        conn.close()
        flash("Voucher not found", "error")
        return redirect(url_for('tosite.list_vouchers', filename=filename))
    
    # Parse JSON data
    json_data = {}
    if voucher['json']:
        try:
            json_data = json.loads(voucher['json'])
        except:
            pass
    
    # Get partner details if exists
    partner = None
    if voucher['kumppani']:
        cursor.execute("SELECT id, nimi FROM Kumppani WHERE id = ?", (voucher['kumppani'],))
        partner = cursor.fetchone()
    
    # Get transactions
    cursor.execute("""
        SELECT id, rivi, tili, kohdennus, selite, debetsnt, kreditsnt, alvprosentti, alvkoodi
        FROM Vienti
        WHERE tosite = ?
        ORDER BY rivi
    """, (voucher_id,))
    
    transactions = []
    total_debit = 0
    total_credit = 0
    
    for row in cursor.fetchall():
        debit_amount = row['debetsnt'] / 100 if row['debetsnt'] else 0
        credit_amount = row['kreditsnt'] / 100 if row['kreditsnt'] else 0
        
        total_debit += debit_amount
        total_credit += credit_amount
        
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
        
        # Get allocation details
        allocation_name = ""
        if row['kohdennus']:
            cursor.execute("SELECT json FROM Kohdennus WHERE id = ?", (row['kohdennus'],))
            allocation_result = cursor.fetchone()
            if allocation_result and allocation_result['json']:
                try:
                    allocation_data = json.loads(allocation_result['json'])
                    allocation_name = allocation_data.get('nimi', {}).get('fi', '')
                except:
                    pass
        
        transactions.append({
            'id': row['id'],
            'rivi': row['rivi'],
            'tili': row['tili'],
            'tili_nimi': account_name,
            'kohdennus': row['kohdennus'],
            'kohdennus_nimi': allocation_name,
            'selite': row['selite'],
            'debit': debit_amount,
            'credit': credit_amount,
            'alv_percent': row['alvprosentti'],
            'alv_code': row['alvkoodi']
        })
    
    # Get attachments
    cursor.execute("""
        SELECT id, nimi, roolinimi, tyyppi
        FROM Liite
        WHERE tosite = ?
        ORDER BY roolinimi
    """, (voucher_id,))
    
    attachments = []
    for row in cursor.fetchall():
        attachments.append({
            'id': row['id'],
            'nimi': row['nimi'],
            'roolinimi': row['roolinimi'],
            'tyyppi': row['tyyppi']
        })
    
    # Check if voucher is balanced
    is_balanced = abs(total_debit - total_credit) < 0.01
    
    conn.close()
    
    return render_template('vouchers/view.html',
                          filename=filename,
                          voucher=voucher,
                          json_data=json_data,
                          partner=partner,
                          transactions=transactions,
                          voucher_type=VOUCHER_TYPES.get(voucher['tyyppi'], "Muu"),
                          status=VOUCHER_STATUSES.get(voucher['tila'], "Tuntematon"),
                          total_debit=f"{total_debit:.2f}",
                          total_credit=f"{total_credit:.2f}",
                          is_balanced=is_balanced,
                          attachments=attachments)

@tosite_bp.route('/db/<filename>/vouchers/<int:voucher_id>/attachment/<int:attachment_id>')
def view_attachment(filename, voucher_id, attachment_id):
    """View a voucher attachment"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Get attachment details
    cursor.execute("""
        SELECT nimi, tyyppi, data 
        FROM Liite 
        WHERE id = ? AND tosite = ?
    """, (attachment_id, voucher_id))
    
    attachment = cursor.fetchone()
    
    if not attachment:
        conn.close()
        flash("Attachment not found", "error")
        return redirect(url_for('tosite.view_voucher', filename=filename, voucher_id=voucher_id))
    
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

@tosite_bp.route('/db/<filename>/vouchers/<int:voucher_id>/delete', methods=['POST'])
def delete_voucher(filename, voucher_id):
    """Delete a voucher"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    
    try:
        # Delete voucher - transactions will be deleted automatically due to CASCADE constraint
        conn.execute("DELETE FROM Tosite WHERE id = ?", (voucher_id,))
        conn.commit()
        flash("Voucher deleted successfully", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error deleting voucher: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('tosite.list_vouchers', filename=filename))

# Function to get VAT account suggestion for a specific VAT code
@tosite_bp.route('/db/<filename>/vat-account-suggestion/<int:vat_code>')
def vat_account_suggestion(filename, vat_code):
    """Return suggested account for a VAT code"""
    # This is a simplified mapping based on Finnish accounting practices
    # In a real application, this might come from database configuration
    vat_account_mapping = {
        1: 2940,  # Standard VAT rate (24%) -> VAT Payable account
        2: 2941,  # Reduced VAT rate 1 (14%) -> VAT Payable account
        3: 2942,  # Reduced VAT rate 2 (10%) -> VAT Payable account
        5: 2930,  # EU sales -> Intra-Community VAT
        6: 2931,  # EU purchases -> VAT on EU Acquisitions
        7: 2921,  # Reverse charge purchases -> VAT on construction services
        8: 2922,  # Reverse charge sales -> VAT on construction service sales
        9: 2939   # Imports -> Import VAT
    }
    
    suggested_account = vat_account_mapping.get(vat_code, 2300)  # Default to general VAT account
    
    # Check if account exists in this database
    db_path = os.path.join('databases', filename)
    if os.path.exists(db_path):
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT numero FROM Tili WHERE numero = ?", (suggested_account,))
        if not cursor.fetchone():
            # If suggested account doesn't exist, find a general VAT account
            cursor.execute("SELECT numero FROM Tili WHERE numero BETWEEN 2300 AND 2999 LIMIT 1")
            result = cursor.fetchone()
            if result:
                suggested_account = result['numero']
        
        conn.close()
    
    return jsonify({"account": suggested_account})

@tosite_bp.route('/db/<filename>/vouchers/<int:voucher_id>/confirm', methods=['POST'])
def confirm_voucher(filename, voucher_id):
    """Confirm a voucher, changing it from Draft to Ready status"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Check if voucher exists and is in Draft status
    cursor.execute("SELECT id, tila FROM Tosite WHERE id = ?", (voucher_id,))
    voucher = cursor.fetchone()
    
    if not voucher:
        conn.close()
        flash("Voucher not found", "error")
        return redirect(url_for('tosite.list_vouchers', filename=filename))
    
    # Check if the voucher is in draft status (0)
    if voucher['tila'] != 0:
        conn.close()
        flash("Only draft vouchers can be confirmed", "warning")
        return redirect(url_for('tosite.view_voucher', filename=filename, voucher_id=voucher_id))
    
    # Check if the voucher has balanced transactions
    cursor.execute("""
        SELECT SUM(debetsnt) as total_debit, SUM(kreditsnt) as total_credit
        FROM Vienti
        WHERE tosite = ?
    """, (voucher_id,))
    
    totals = cursor.fetchone()
    total_debit = totals['total_debit'] or 0
    total_credit = totals['total_credit'] or 0
    
    # If the difference is more than 1 cent, it's unbalanced
    if abs(total_debit - total_credit) > 1:
        conn.close()
        flash("Voucher cannot be confirmed because it is unbalanced", "error")
        return redirect(url_for('tosite.view_voucher', filename=filename, voucher_id=voucher_id))
    
    try:
        # Update the voucher status to Ready (100)
        conn.execute("UPDATE Tosite SET tila = 100 WHERE id = ?", (voucher_id,))
        conn.commit()
        flash("Voucher has been confirmed successfully", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error confirming voucher: {str(e)}", "error")
    
    conn.close()
    return redirect(url_for('tosite.view_voucher', filename=filename, voucher_id=voucher_id))

def register_blueprint(app):
    """Register the blueprint with the main Flask app"""
    app.register_blueprint(tosite_bp) 