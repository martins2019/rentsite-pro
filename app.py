import os
import sqlite3
import json
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rentsite.db')


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    # Habilita WAL mode para melhor performance concorrente
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Tabela de Configurações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
    ''')

    # Tabela de Clientes/Aluguéis
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            site_name TEXT,
            domain TEXT NOT NULL,
            value REAL NOT NULL,
            billing_period TEXT NOT NULL DEFAULT 'monthly',
            due_day INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            last_payment TEXT,
            pix TEXT,
            updated_at INTEGER NOT NULL
        )
    ''')

    # Dados Iniciais do Gestor
    cursor.execute("SELECT COUNT(*) FROM settings")
    if cursor.fetchone()[0] == 0:
        now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        default_settings = [
            ('managerName', 'Sua Empresa Web', now_ts),
            ('managerPhone', '5511999998888', now_ts),
            ('defaultPix', 'seu-email-pix@dominio.com', now_ts)
        ]
        cursor.executemany("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)", default_settings)

    # Dados Iniciais de Exemplo (se vazio)
    cursor.execute("SELECT COUNT(*) FROM clients")
    if cursor.fetchone()[0] == 0:
        now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        mock_clients = [
            ('1', 'Carlos Oliveira', '5511987654321', 'Loja Garimpo', 'garimpoweb.com', 150.00, 'monthly', 10, 'active', '2026-09-10', '', now_ts),
            ('2', 'Dra. Amanda Silva', '5511976543210', 'Consultório Odonto', 'draamandasilva.com.br', 220.00, 'monthly', 25, 'overdue', '2026-08-25', '', now_ts)
        ]
        cursor.executemany('''
            INSERT INTO clients 
            (id, name, phone, site_name, domain, value, billing_period, due_day, status, last_payment, pix, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', mock_clients)

    conn.commit()
    conn.close()


# Initialize database schema
init_db()


# -------------------------------------------------------------------
# ROTAS DE PÁGINA
# -------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


# -------------------------------------------------------------------
# API ENDPOINTS (CRUD & CONFIGS)
# -------------------------------------------------------------------

@app.route('/api/settings', methods=['GET'])
def get_settings():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value, updated_at FROM settings")
    rows = cursor.fetchall()
    conn.close()

    settings_dict = {}
    for r in rows:
        settings_dict[r['key']] = r['value']

    return jsonify(settings_dict)


@app.route('/api/settings', methods=['POST'])
def save_settings():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    for key in ['managerName', 'managerPhone', 'defaultPix']:
        if key in data:
            cursor.execute('''
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            ''', (key, str(data[key]), now_ts))

    conn.commit()
    conn.close()
    return jsonify({"status": "success", "updated_at": now_ts})


@app.route('/api/clients', methods=['GET'])
def get_clients():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM clients ORDER BY name ASC")
    rows = cursor.fetchall()
    conn.close()

    clients_list = []
    for r in rows:
        clients_list.append({
            "id": r["id"],
            "name": r["name"],
            "phone": r["phone"],
            "siteName": r["site_name"],
            "domain": r["domain"],
            "value": r["value"],
            "billingPeriod": r["billing_period"],
            "dueDay": r["due_day"],
            "status": r["status"],
            "lastPayment": r["last_payment"],
            "pix": r["pix"],
            "updatedAt": r["updated_at"]
        })

    return jsonify(clients_list)


@app.route('/api/clients', methods=['POST'])
def save_client():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    client_id = data.get('id') or str(now_ts)

    cursor.execute('''
        INSERT INTO clients (id, name, phone, site_name, domain, value, billing_period, due_day, status, last_payment, pix, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            phone=excluded.phone,
            site_name=excluded.site_name,
            domain=excluded.domain,
            value=excluded.value,
            billing_period=excluded.billing_period,
            due_day=excluded.due_day,
            status=excluded.status,
            last_payment=excluded.last_payment,
            pix=excluded.pix,
            updated_at=excluded.updated_at
    ''', (
        client_id,
        data['name'],
        data['phone'],
        data.get('siteName', ''),
        data['domain'],
        float(data['value']),
        data.get('billingPeriod', 'monthly'),
        int(data['dueDay']),
        data.get('status', 'active'),
        data.get('lastPayment', ''),
        data.get('pix', ''),
        now_ts
    ))

    conn.commit()
    conn.close()
    return jsonify({"status": "success", "id": client_id, "updatedAt": now_ts})


@app.route('/api/clients/<client_id>', methods=['DELETE'])
def delete_client(client_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


# -------------------------------------------------------------------
# MOTOR DE SINCRONIZAÇÃO MULTI-MÁQUINA (EXPORT / IMPORT / SYNC)
# -------------------------------------------------------------------

@app.route('/api/sync/export', methods=['GET'])
def sync_export():
    """Retorna toda a base com timestamps para ser enviada a outra máquina."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM settings")
    settings_rows = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM clients")
    clients_rows = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return jsonify({
        "serverTime": int(datetime.now(timezone.utc).timestamp() * 1000),
        "settings": settings_rows,
        "clients": clients_rows
    })


@app.route('/api/sync/import', methods=['POST'])
def sync_import():
    """
    Sincroniza os dados recebidos com a base local.
    Aplica a regra 'Última Alteração Vence' (Last-Write-Wins) baseada no updated_at.
    """
    payload = request.json
    conn = get_db()
    cursor = conn.cursor()

    updated_clients = 0
    updated_settings = 0

    # Sincroniza Configurações
    if 'settings' in payload:
        for s in payload['settings']:
            key = s.get('key')
            val = s.get('value')
            remote_ts = s.get('updated_at', 0)

            cursor.execute("SELECT updated_at FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()

            if not row or remote_ts > row['updated_at']:
                cursor.execute('''
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                ''', (key, val, remote_ts))
                updated_settings += 1

    # Sincroniza Clientes
    if 'clients' in payload:
        for c in payload['clients']:
            c_id = c.get('id')
            remote_ts = c.get('updated_at', 0)

            cursor.execute("SELECT updated_at FROM clients WHERE id = ?", (c_id,))
            row = cursor.fetchone()

            if not row or remote_ts > row['updated_at']:
                cursor.execute('''
                    INSERT INTO clients (id, name, phone, site_name, domain, value, billing_period, due_day, status, last_payment, pix, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        phone=excluded.phone,
                        site_name=excluded.site_name,
                        domain=excluded.domain,
                        value=excluded.value,
                        billing_period=excluded.billing_period,
                        due_day=excluded.due_day,
                        status=excluded.status,
                        last_payment=excluded.last_payment,
                        pix=excluded.pix,
                        updated_at=excluded.updated_at
                ''', (
                    c_id, c['name'], c['phone'], c.get('site_name', ''), c['domain'],
                    float(c['value']), c.get('billing_period', 'monthly'), int(c['due_day']),
                    c.get('status', 'active'), c.get('last_payment', ''), c.get('pix', ''), remote_ts
                ))
                updated_clients += 1

    conn.commit()
    conn.close()

    return jsonify({
        "status": "success",
        "syncedSettings": updated_settings,
        "syncedClients": updated_clients
    })


if __name__ == '__main__':
    print("🚀 RentSite Pro iniciado em http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)