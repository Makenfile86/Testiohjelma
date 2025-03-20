import sqlite3
import json
import os
import uuid
import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory

app = Flask(__name__)
app.secret_key = os.urandom(24)  # For flash messages

class AccountingDatabaseCreator:
    """
    Creates a SQLite database for accounting purposes, inspired by Kitsas/Kitupiikki.
    Each client gets their own database file.
    """
    
    def __init__(self):
        self.db_connection = None
        self.db_cursor = None
        self.db_path = None
    
    def create_database(self, client_info, output_directory="databases"):
        """
        Create a new SQLite database for a client with the provided information.
        
        Args:
            client_info (dict): Dictionary containing client information
            output_directory (str): Directory where database files will be stored
        
        Returns:
            str: Path to the created database file
        """
        # Create output directory if it doesn't exist
        Path(output_directory).mkdir(parents=True, exist_ok=True)
        
        # Generate a safe filename from the client name
        safe_name = client_info.get('name', 'client').lower().replace(' ', '_')
        file_name = f"{safe_name}_{uuid.uuid4().hex[:8]}.db"
        self.db_path = os.path.join(output_directory, file_name)
        
        # Create and connect to the database
        self.db_connection = sqlite3.connect(self.db_path)
        self.db_cursor = self.db_connection.cursor()
        
        # Enable foreign keys
        self.db_cursor.execute("PRAGMA foreign_keys = ON")
        
        # Create the database schema
        self._create_schema()
        
        # Insert client information
        self._insert_client_info(client_info)
        
        # Create default fiscal period if provided
        if 'fiscal_period' in client_info and client_info['fiscal_period'].get('start_date') and client_info['fiscal_period'].get('end_date'):
            self._create_fiscal_period(client_info['fiscal_period'])
        
        # Create default chart of accounts
        self._create_default_chart_of_accounts()
        
        # Commit changes and close connection
        self.db_connection.commit()
        self.db_connection.close()
        
        return self.db_path
    
    def _create_schema(self):
        """Create the database schema with all necessary tables"""
        
        # Settings table (Asetus)
        self.db_cursor.execute('''
        CREATE TABLE Asetus (
            avain VARCHAR(128) PRIMARY KEY NOT NULL,
            arvo TEXT,
            muokattu TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Account table (Tili)
        self.db_cursor.execute('''
        CREATE TABLE Tili (
            numero INTEGER PRIMARY KEY NOT NULL,
            tyyppi VARCHAR(10) NOT NULL,
            iban VARCHAR(32),
            json TEXT,
            muokattu TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Header table (Otsikko)
        self.db_cursor.execute('''
        CREATE TABLE Otsikko (
            numero INTEGER NOT NULL,
            taso INTEGER NOT NULL,
            json TEXT,
            muokattu TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (numero, taso)
        )
        ''')
        
        # Fiscal period table (Tilikausi)
        self.db_cursor.execute('''
        CREATE TABLE Tilikausi (
            alkaa DATE PRIMARY KEY NOT NULL,
            loppuu DATE UNIQUE NOT NULL,
            json TEXT
        )
        ''')
        
        # Allocation table (Kohdennus)
        self.db_cursor.execute('''
        CREATE TABLE Kohdennus (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            tyyppi INTEGER NOT NULL,
            kuuluu INTEGER REFERENCES Kohdennus(id) ON DELETE RESTRICT,
            json TEXT,
            CHECK (tyyppi IN (0,1,2,3))
        )
        ''')
        
        # Insert default allocation
        self.db_cursor.execute('''
        INSERT INTO Kohdennus (id, tyyppi, json) VALUES 
        (0, 0, '{"nimi":{"fi":"Yleinen","en":"General"}}')
        ''')
        
        # Budget table (Budjetti)
        self.db_cursor.execute('''
        CREATE TABLE Budjetti (
            tilikausi DATE REFERENCES Tilikausi(alkaa),
            kohdennus INTEGER REFERENCES Kohdennus(id) ON DELETE CASCADE,
            tili INTEGER REFERENCES Tili(numero) ON DELETE CASCADE,
            sentti BIGINT,
            PRIMARY KEY (tilikausi, kohdennus, tili)
        )
        ''')
        
        # Partner table (Kumppani)
        self.db_cursor.execute('''
        CREATE TABLE Kumppani (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            nimi VARCHAR(255),
            alvtunnus VARCHAR(20),
            json TEXT
        )
        ''')
        
        self.db_cursor.execute('CREATE INDEX kumppani_nimi_index ON Kumppani(nimi)')
        
        # Partner IBAN table (KumppaniIban)
        self.db_cursor.execute('''
        CREATE TABLE KumppaniIban (
            iban VARCHAR(30) PRIMARY KEY NOT NULL,
            kumppani INTEGER REFERENCES Kumppani(id) ON DELETE CASCADE
        )
        ''')
        
        # Insert tax authority as default partner
        self.db_cursor.execute('''
        INSERT INTO Kumppani(nimi, alvtunnus, json) VALUES 
        ('Tax Authority', '', '{"osoite":"Tax Office","postinumero":"00000","kaupunki":"Tax City"}')
        ''')
        
        # Voucher table (Tosite)
        self.db_cursor.execute('''
        CREATE TABLE Tosite (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            pvm DATE,
            tyyppi INTEGER,
            tila INTEGER DEFAULT 100,
            tunniste INTEGER,
            sarja VARCHAR(10),
            otsikko TEXT,
            kumppani INTEGER REFERENCES Kumppani(id),
            laskupvm DATE,
            erapvm DATE,
            viite VARCHAR(64),
            json TEXT
        )
        ''')
        
        self.db_cursor.execute('CREATE INDEX tosite_pvm ON Tosite(pvm)')
        self.db_cursor.execute('CREATE INDEX tosite_tyyppi ON Tosite(tyyppi)')
        self.db_cursor.execute('CREATE INDEX tosite_tila ON Tosite(tila)')
        
        # Transaction table (Vienti)
        self.db_cursor.execute('''
        CREATE TABLE Vienti (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            rivi INTEGER NOT NULL,
            tosite INTEGER REFERENCES Tosite(id) ON DELETE CASCADE,
            tyyppi INTEGER DEFAULT 0,
            pvm DATE,
            tili INTEGER REFERENCES Tili(numero) ON DELETE RESTRICT,
            kohdennus INTEGER DEFAULT 0 REFERENCES Kohdennus(id) ON DELETE RESTRICT,
            selite TEXT,
            debetsnt BIGINT,
            kreditsnt BIGINT,
            eraid INTEGER,
            alvprosentti NUMERIC(5,2),
            alvkoodi INTEGER,
            kumppani INTEGER REFERENCES Kumppani(id),
            jaksoalkaa DATE,
            jaksoloppuu DATE,
            arkistotunnus VARCHAR(32),
            json TEXT,
            CHECK (debetsnt = 0 OR kreditsnt = 0)
        )
        ''')
        
        self.db_cursor.execute('CREATE INDEX vienti_tosite ON Vienti(tosite)')
        self.db_cursor.execute('CREATE INDEX vienti_pvm ON Vienti(pvm)')
        self.db_cursor.execute('CREATE INDEX vienti_tili ON Vienti(tili)')
        self.db_cursor.execute('CREATE INDEX vienti_kohdennus ON Vienti(kohdennus)')
        
        # Attachment table (Liite)
        self.db_cursor.execute('''
        CREATE TABLE Liite (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            tosite INTEGER REFERENCES Tosite(id) ON DELETE CASCADE,
            nimi TEXT,
            roolinimi VARCHAR(16),
            tyyppi TEXT,
            sha TEXT,
            data BLOB,
            luotu TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            json TEXT,
            UNIQUE(tosite, roolinimi)
        )
        ''')
        
        self.db_cursor.execute('CREATE INDEX liite_tosite ON Liite(tosite)')
        
        # Product table (Tuote)
        self.db_cursor.execute('''
        CREATE TABLE Tuote (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            nimike VARCHAR(255),
            json TEXT
        )
        ''')
        
        # Line table (Rivi)
        self.db_cursor.execute('''
        CREATE TABLE Rivi (
            tosite INTEGER REFERENCES Tosite(id) ON DELETE CASCADE,
            rivi INTEGER,
            tuote INTEGER,
            myyntikpl REAL,
            ostokpl REAL,
            ahinta REAL DEFAULT 0.0,
            json TEXT,
            PRIMARY KEY(tosite, rivi)
        )
        ''')
        
        self.db_cursor.execute('CREATE INDEX rivi_tosite ON Rivi(tosite)')
    
    def _insert_client_info(self, client_info):
        """Insert client information into the settings table"""
        
        # Basic client information
        settings = {
            "Nimi": client_info.get('name', ''),
            "Ytunnus": client_info.get('business_id', ''),
            "Katuosoite": client_info.get('street_address', ''),
            "Postinumero": client_info.get('postal_code', ''),
            "Kaupunki": client_info.get('city', ''),
            "Kotipaikka": client_info.get('domicile', ''),
            "Email": client_info.get('email', ''),
            "Kotisivu": client_info.get('website', ''),
            "Puhelin": client_info.get('phone', ''),
            "AlvVelvollinen": "ON" if client_info.get('vat_registered', False) else "",
            "Yritysmuoto": client_info.get('company_form', ''),
            "TilikarttaLaajuus": client_info.get('chart_scope', 'basic'),
            
            # System information
            "KpVersio": "1.0",
            "LuotuVersiolla": "1.0",
            "Luotu": datetime.datetime.now().isoformat(),
            "UID": str(uuid.uuid4()),
            "LaskuSeuraavaId": 100
        }
        
        # Insert each setting
        for key, value in settings.items():
            if value:  # Only insert non-empty values
                self.db_cursor.execute(
                    "INSERT INTO Asetus (avain, arvo) VALUES (?, ?)",
                    (key, value)
                )
        
        # If bank account is provided, add it to the settings
        if 'iban' in client_info and client_info['iban']:
            self.db_cursor.execute(
                "INSERT INTO Asetus (avain, arvo) VALUES (?, ?)",
                ("LaskuIbanit", client_info['iban'])
            )
    
    def _create_fiscal_period(self, fiscal_period):
        """Create a fiscal period entry"""
        start_date = fiscal_period.get('start_date')
        end_date = fiscal_period.get('end_date')
        
        if start_date and end_date:
            # Additional information as JSON
            json_data = json.dumps({
                "nimi": fiscal_period.get('name', 'Fiscal Period')
            })
            
            self.db_cursor.execute(
                "INSERT INTO Tilikausi (alkaa, loppuu, json) VALUES (?, ?, ?)",
                (start_date, end_date, json_data)
            )
    
    def _create_default_chart_of_accounts(self):
        """Create a basic chart of accounts"""
        
        # Get the chart scope from settings
        chart_scope = 'basic'  # Default
        try:
            self.db_cursor.execute("SELECT arvo FROM Asetus WHERE avain='TilikarttaLaajuus'")
            result = self.db_cursor.fetchone()
            if result and result[0]:
                chart_scope = result[0]
        except:
            pass
        
        # Define basic accounts (available in all scopes)
        accounts = [
            # Assets
            (1000, "A", None, {"nimi": "Cash", "tyyppi": "Asset"}),
            (1200, "A", None, {"nimi": "Accounts Receivable", "tyyppi": "Asset"}),
            (1500, "A", None, {"nimi": "Inventory", "tyyppi": "Asset"}),
            
            # Liabilities
            (2000, "B", None, {"nimi": "Accounts Payable", "tyyppi": "Liability"}),
            (2300, "B", None, {"nimi": "VAT Payable", "tyyppi": "Liability"}),
            (2800, "B", None, {"nimi": "Loans", "tyyppi": "Liability"}),
            
            # Equity
            (3000, "C", None, {"nimi": "Equity", "tyyppi": "Equity"}),
            (3900, "C", None, {"nimi": "Retained Earnings", "tyyppi": "Equity"}),
            
            # Income
            (4000, "D", None, {"nimi": "Sales Revenue", "tyyppi": "Income"}),
            (4500, "D", None, {"nimi": "Other Income", "tyyppi": "Income"}),
            
            # Expenses
            (5000, "E", None, {"nimi": "Cost of Goods Sold", "tyyppi": "Expense"}),
            (6000, "E", None, {"nimi": "Salaries", "tyyppi": "Expense"}),
            (6500, "E", None, {"nimi": "Rent", "tyyppi": "Expense"}),
            (7000, "E", None, {"nimi": "Utilities", "tyyppi": "Expense"}),
            (8000, "E", None, {"nimi": "Other Expenses", "tyyppi": "Expense"})
        ]
        
        # Add standard accounts if scope is standard or extended
        if chart_scope in ['standard', 'extended']:
            standard_accounts = [
                # More detailed assets
                (1010, "A", None, {"nimi": "Petty Cash", "tyyppi": "Asset"}),
                (1100, "A", None, {"nimi": "Bank Account", "tyyppi": "Asset"}),
                (1300, "A", None, {"nimi": "Loans Receivable", "tyyppi": "Asset"}),
                (1400, "A", None, {"nimi": "Prepaid Expenses", "tyyppi": "Asset"}),
                (1600, "A", None, {"nimi": "Fixed Assets", "tyyppi": "Asset"}),
                (1610, "A", None, {"nimi": "Accumulated Depreciation", "tyyppi": "Asset"}),
                
                # More detailed liabilities
                (2100, "B", None, {"nimi": "Notes Payable", "tyyppi": "Liability"}),
                (2200, "B", None, {"nimi": "Accrued Expenses", "tyyppi": "Liability"}),
                (2310, "B", None, {"nimi": "VAT Sales", "tyyppi": "Liability"}),
                (2320, "B", None, {"nimi": "VAT Purchases", "tyyppi": "Liability"}),
                (2400, "B", None, {"nimi": "Employee Withholdings", "tyyppi": "Liability"}),
                
                # More detailed income
                (4100, "D", None, {"nimi": "Product Sales", "tyyppi": "Income"}),
                (4200, "D", None, {"nimi": "Service Sales", "tyyppi": "Income"}),
                (4300, "D", None, {"nimi": "Discounts Given", "tyyppi": "Income"}),
                
                # More detailed expenses
                (6100, "E", None, {"nimi": "Payroll Taxes", "tyyppi": "Expense"}),
                (6200, "E", None, {"nimi": "Employee Benefits", "tyyppi": "Expense"}),
                (6600, "E", None, {"nimi": "Office Supplies", "tyyppi": "Expense"}),
                (6700, "E", None, {"nimi": "Marketing & Advertising", "tyyppi": "Expense"}),
                (6800, "E", None, {"nimi": "Professional Services", "tyyppi": "Expense"}),
                (6900, "E", None, {"nimi": "Travel Expenses", "tyyppi": "Expense"}),
                (7100, "E", None, {"nimi": "Telephone & Internet", "tyyppi": "Expense"}),
                (7200, "E", None, {"nimi": "Insurance", "tyyppi": "Expense"}),
                (7300, "E", None, {"nimi": "Depreciation Expense", "tyyppi": "Expense"}),
                (7500, "E", None, {"nimi": "Maintenance & Repairs", "tyyppi": "Expense"})
            ]
            accounts.extend(standard_accounts)
        
        # Add extended accounts if scope is extended
        if chart_scope == 'extended':
            extended_accounts = [
                # Even more detailed assets
                (1020, "A", None, {"nimi": "Cash in Foreign Currency", "tyyppi": "Asset"}),
                (1110, "A", None, {"nimi": "Savings Account", "tyyppi": "Asset"}),
                (1120, "A", None, {"nimi": "Investment Account", "tyyppi": "Asset"}),
                (1210, "A", None, {"nimi": "Accounts Receivable - Domestic", "tyyppi": "Asset"}),
                (1220, "A", None, {"nimi": "Accounts Receivable - Foreign", "tyyppi": "Asset"}),
                (1310, "A", None, {"nimi": "Short-term Loans Receivable", "tyyppi": "Asset"}),
                (1320, "A", None, {"nimi": "Long-term Loans Receivable", "tyyppi": "Asset"}),
                (1510, "A", None, {"nimi": "Raw Materials Inventory", "tyyppi": "Asset"}),
                (1520, "A", None, {"nimi": "Work in Progress Inventory", "tyyppi": "Asset"}),
                (1530, "A", None, {"nimi": "Finished Goods Inventory", "tyyppi": "Asset"}),
                (1620, "A", None, {"nimi": "Equipment", "tyyppi": "Asset"}),
                (1630, "A", None, {"nimi": "Vehicles", "tyyppi": "Asset"}),
                (1640, "A", None, {"nimi": "Buildings", "tyyppi": "Asset"}),
                (1650, "A", None, {"nimi": "Land", "tyyppi": "Asset"}),
                (1700, "A", None, {"nimi": "Intangible Assets", "tyyppi": "Asset"}),
                (1710, "A", None, {"nimi": "Goodwill", "tyyppi": "Asset"}),
                (1720, "A", None, {"nimi": "Patents & Trademarks", "tyyppi": "Asset"}),
                
                # Even more detailed liabilities
                (2010, "B", None, {"nimi": "Accounts Payable - Domestic", "tyyppi": "Liability"}),
                (2020, "B", None, {"nimi": "Accounts Payable - Foreign", "tyyppi": "Liability"}),
                (2210, "B", None, {"nimi": "Wages Payable", "tyyppi": "Liability"}),
                (2220, "B", None, {"nimi": "Interest Payable", "tyyppi": "Liability"}),
                (2500, "B", None, {"nimi": "Income Tax Payable", "tyyppi": "Liability"}),
                (2600, "B", None, {"nimi": "Current Portion of Long-term Debt", "tyyppi": "Liability"}),
                (2810, "B", None, {"nimi": "Bank Loans", "tyyppi": "Liability"}),
                (2820, "B", None, {"nimi": "Mortgage Payable", "tyyppi": "Liability"}),
                (2900, "B", None, {"nimi": "Deferred Revenue", "tyyppi": "Liability"}),
                
                # Even more detailed equity
                (3100, "C", None, {"nimi": "Common Stock", "tyyppi": "Equity"}),
                (3200, "C", None, {"nimi": "Preferred Stock", "tyyppi": "Equity"}),
                (3300, "C", None, {"nimi": "Additional Paid-in Capital", "tyyppi": "Equity"}),
                (3400, "C", None, {"nimi": "Treasury Stock", "tyyppi": "Equity"}),
                (3500, "C", None, {"nimi": "Dividends", "tyyppi": "Equity"}),
                
                # Even more detailed income
                (4110, "D", None, {"nimi": "Domestic Sales", "tyyppi": "Income"}),
                (4120, "D", None, {"nimi": "Export Sales", "tyyppi": "Income"}),
                (4130, "D", None, {"nimi": "Online Sales", "tyyppi": "Income"}),
                (4210, "D", None, {"nimi": "Consulting Services", "tyyppi": "Income"}),
                (4220, "D", None, {"nimi": "Maintenance Services", "tyyppi": "Income"}),
                (4600, "D", None, {"nimi": "Interest Income", "tyyppi": "Income"}),
                (4700, "D", None, {"nimi": "Rental Income", "tyyppi": "Income"}),
                (4800, "D", None, {"nimi": "Capital Gains", "tyyppi": "Income"}),
                
                # Even more detailed expenses
                (5100, "E", None, {"nimi": "Raw Materials", "tyyppi": "Expense"}),
                (5200, "E", None, {"nimi": "Direct Labor", "tyyppi": "Expense"}),
                (5300, "E", None, {"nimi": "Manufacturing Overhead", "tyyppi": "Expense"}),
                (5400, "E", None, {"nimi": "Shipping & Freight", "tyyppi": "Expense"}),
                (6110, "E", None, {"nimi": "Executive Salaries", "tyyppi": "Expense"}),
                (6120, "E", None, {"nimi": "Staff Salaries", "tyyppi": "Expense"}),
                (6130, "E", None, {"nimi": "Commissions", "tyyppi": "Expense"}),
                (6140, "E", None, {"nimi": "Bonuses", "tyyppi": "Expense"}),
                (6210, "E", None, {"nimi": "Health Insurance", "tyyppi": "Expense"}),
                (6220, "E", None, {"nimi": "Retirement Benefits", "tyyppi": "Expense"}),
                (6550, "E", None, {"nimi": "Equipment Rental", "tyyppi": "Expense"}),
                (6710, "E", None, {"nimi": "Advertising - Print", "tyyppi": "Expense"}),
                (6720, "E", None, {"nimi": "Advertising - Digital", "tyyppi": "Expense"}),
                (6730, "E", None, {"nimi": "Trade Shows & Events", "tyyppi": "Expense"}),
                (6810, "E", None, {"nimi": "Legal Fees", "tyyppi": "Expense"}),
                (6820, "E", None, {"nimi": "Accounting Fees", "tyyppi": "Expense"}),
                (6830, "E", None, {"nimi": "Consulting Fees", "tyyppi": "Expense"}),
                (7400, "E", None, {"nimi": "Bank Fees & Charges", "tyyppi": "Expense"}),
                (7600, "E", None, {"nimi": "Interest Expense", "tyyppi": "Expense"}),
                (7700, "E", None, {"nimi": "Research & Development", "tyyppi": "Expense"}),
                (7800, "E", None, {"nimi": "Training & Education", "tyyppi": "Expense"}),
                (7900, "E", None, {"nimi": "Licenses & Permits", "tyyppi": "Expense"}),
                (8100, "E", None, {"nimi": "Foreign Exchange Loss", "tyyppi": "Expense"}),
                (8200, "E", None, {"nimi": "Bad Debt Expense", "tyyppi": "Expense"}),
                (8500, "E", None, {"nimi": "Income Tax Expense", "tyyppi": "Expense"})
            ]
            accounts.extend(extended_accounts)
        
        # Insert accounts
        for numero, tyyppi, iban, json_data in accounts:
            self.db_cursor.execute(
                "INSERT INTO Tili (numero, tyyppi, iban, json) VALUES (?, ?, ?, ?)",
                (numero, tyyppi, iban, json.dumps(json_data))
            )
            
        # Define account headers
        headers = [
            (1000, 1, {"nimi": "ASSETS"}),
            (2000, 1, {"nimi": "LIABILITIES"}),
            (3000, 1, {"nimi": "EQUITY"}),
            (4000, 1, {"nimi": "INCOME"}),
            (5000, 1, {"nimi": "EXPENSES"})
        ]
        
        # Insert headers
        for numero, taso, json_data in headers:
            self.db_cursor.execute(
                "INSERT INTO Otsikko (numero, taso, json) VALUES (?, ?, ?)",
                (numero, taso, json.dumps(json_data))
            )


# Flask routes
@app.route('/')
def index():
    """Home page with list of existing databases"""
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
                conn = sqlite3.connect(file)
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
    
    return render_template('index.html', databases=databases)

@app.route('/create', methods=['GET', 'POST'])
def create_database():
    """Create a new client database"""
    if request.method == 'POST':
        # Collect form data
        client_info = {
            'name': request.form.get('name'),
            'business_id': request.form.get('business_id'),
            'street_address': request.form.get('street_address'),
            'postal_code': request.form.get('postal_code'),
            'city': request.form.get('city'),
            'domicile': request.form.get('domicile'),
            'email': request.form.get('email'),
            'phone': request.form.get('phone'),
            'website': request.form.get('website'),
            'iban': request.form.get('iban'),
            'vat_registered': request.form.get('vat_registered') == 'yes',
            'company_form': request.form.get('company_form'),
            'chart_scope': request.form.get('chart_scope')
        }
        
        # Fiscal period
        if request.form.get('setup_fiscal') == 'yes':
            client_info['fiscal_period'] = {
                'name': request.form.get('fiscal_name'),
                'start_date': request.form.get('fiscal_start'),
                'end_date': request.form.get('fiscal_end')
            }
        
        # Create the database
        creator = AccountingDatabaseCreator()
        try:
            db_path = creator.create_database(client_info)
            flash(f"Database created successfully: {os.path.basename(db_path)}", "success")
            return redirect(url_for('index'))
        except Exception as e:
            flash(f"Error creating database: {str(e)}", "error")
            return render_template('create.html', client_info=client_info)
    
    return render_template('create.html')

@app.route('/download/<filename>')
def download_database(filename):
    """Download a database file"""
    return send_from_directory('databases', filename, as_attachment=True)

@app.route('/view/<filename>')
def view_database(filename):
    """View database details"""
    db_path = os.path.join('databases', filename)
    
    if not os.path.exists(db_path):
        flash("Database file not found", "error")
        return redirect(url_for('index'))
    
    # Get database information
    client_info = {}
    tables = []
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get client information
        cursor.execute("SELECT avain, arvo FROM Asetus")
        for key, value in cursor.fetchall():
            client_info[key] = value
        
        # Get table list
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = [row[0] for row in cursor.fetchall()]
        
        # Get record counts for each table
        for table in table_names:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            tables.append({'name': table, 'count': count})
        
        conn.close()
    except Exception as e:
        flash(f"Error reading database: {str(e)}", "error")
        return redirect(url_for('index'))
    
    return render_template('view.html', filename=filename, client_info=client_info, tables=tables)

# Import and register the tosite blueprint
import tosite
tosite.register_blueprint(app)

# Import and register the tili blueprint
import tili
tili.register_blueprint(app)

# Import and register the tilinavaus blueprint
import tilinavaus
tilinavaus.register_blueprint(app)

# Import and register the asetukset blueprint
import asetukset
asetukset.register_blueprint(app)

# Import and register the laskut blueprint
import laskut
laskut.register_blueprint(app)

if __name__ == '__main__':
    app.run(debug=True)