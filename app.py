from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
import sqlite3
from werkzeug.utils import secure_filename
import json
import os
import sys

app = Flask(__name__)
app.secret_key = 'secret_key_for_session_management'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chats.db'  # Chemin vers la base de données SQLite
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 Mo max

socketio = SocketIO(app)
db = SQLAlchemy(app)

# Modèle de données pour les messages
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(50), nullable=False)
    group_id = db.Column(db.String(50), nullable=False)
    message = db.Column(db.String(500), nullable=False)

# Dictionnaire pour garder la trace des utilisateurs connectés par groupe
connected_users = {}

# Créer la base de données si elle n'existe pas déjà

with app.app_context():
    db.create_all()

    
# Stockage temporaire du contenu
content_storage = {
    'content': ''
}

# Chargement des fichiers JSON
USERS_FILE = 'users.json'
GROUPS_FILE = 'groups.json'

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Créer le dossier de téléchargement s'il n'existe pas
#if not os.path.exists(app.config['UPLOAD_FOLDER']):
#    os.makedirs(app.config['UPLOAD_FOLDER'])

DATABASE = 'files.db'

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, filename TEXT, user TEXT)''')
    conn.commit()
    conn.close()

#Charge les utilisateurs depuis un fichier JSON
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as file:
            return json.load(file)
    return []

#Enregistre les utilisateurs depuis un fichier JSON
def save_users(users):
    with open(USERS_FILE, 'w') as file:
        json.dump(users, file)

#Charge les groupes depuis un fichier JSON
def load_groups():
    if os.path.exists(GROUPS_FILE):
        with open(GROUPS_FILE, 'r') as file:
            return json.load(file)
    return []

#Enregistre les groupes depuis un fichier JSON
def save_groups(groups):
    with open(GROUPS_FILE, 'w') as file:
        json.dump(groups, file)

@app.route('/')
def home():
    if 'username' in session:
        return redirect(url_for('index'))
    return redirect(url_for('login'))

@app.route('/index')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', username=session['username'])

@app.route('/docs', methods=['GET', 'POST'])
def docs():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('docs.html')

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('chat.html', user=session['username'], group_id=session['groupe'])

@app.route('/files', methods=['GET', 'POST'])
def files():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    

    cursor.execute("SELECT * FROM files")
    files = cursor.fetchall()
    conn.close()
    return render_template('files.html', files=files)


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return redirect(url_for('files'))

    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return redirect(url_for('files'))
    
    filename = secure_filename(file.filename)
    user = session['username']

    # Connexion à la base de données
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Vérifier si le fichier existe déjà pour cet utilisateur
    cursor.execute("SELECT COUNT(*) FROM files WHERE filename = ? AND user = ?", (filename, user))
    count = cursor.fetchone()[0]

    # Si le fichier existe déjà, modifier son nom
    if count > 0:
        name, ext = os.path.splitext(filename)
        i = 1
        new_filename = f"{name}({i}){ext}"
        while True:
            cursor.execute("SELECT COUNT(*) FROM files WHERE filename = ? AND user = ?", (new_filename, user))
            if cursor.fetchone()[0] == 0:
                break
            i += 1
            new_filename = f"{name}({i}){ext}"
        filename = new_filename

    # Sauvegarder le fichier
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

    # Insérer dans la base de données
    cursor.execute("INSERT INTO files (filename, user) VALUES (?, ?)", (filename, user))
    conn.commit()
    conn.close()

    socketio.emit('new_file', {'filename': filename, 'user': user})
    return redirect(url_for('files'))

#Permet de transfere le fichier choisi dans le dossier uploads
@app.route('/uploads/<filename>')
def download_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

#Permet de supprimer un fichier du dossier uploads, on ne peut pas supprimer les fichiers des autres 
@app.route('/delete/<int:file_id>', methods=['POST'])
def delete_file(file_id):
    user = session['username']
    if not user:
        return redirect(url_for('login')) 
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT filename, user FROM files WHERE id = ?", (file_id,))
    file = cursor.fetchone()
    if file and file[1] == user:
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], file[0]))
        cursor.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
        socketio.emit('delete_file', {'file_id': file_id})
    conn.close()
    return redirect(url_for('files'))



#Permet la connexion des utilisateurs
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
    
        users = load_users()
        user = next((u for u in users if u['username'] == username and u['password'] == password), None)

        if user:
            session['username'] = username
            return redirect(url_for('index'))
        else:
            flash('Nom d\'utilisateur ou mot de passe incorrect.', 'error')

    return render_template('login.html')

#Permet l'inscription des utilisateurs
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        users = load_users()
        if any(u['username'] == username for u in users):
            flash('Ce nom d\'utilisateur est déjà pris.', 'error')
        else:
            users.append({'username': username, 'password': password})
            save_users(users)
            flash('Inscription réussie ! Vous pouvez maintenant vous connecter.', 'success')
            return redirect(url_for('login'))

    return render_template('signup.html')


#Permet de créer des groupes
@app.route('/create_group', methods=['GET', 'POST'])
def create_group():
    return render_template('create_group.html')

#Permet de rejoindre des groupes
@app.route('/join_group', methods=['GET', 'POST'])
def join_group():
    return render_template('join_group.html')


@app.route('/new_group', methods=['GET', 'POST'])
def new_group():
    if request.method == 'POST':
        name = request.form['group_name']
        password = request.form['password']

        groupes = load_groups()
        if any(u['group_name'] == name for u in groupes):
            flash('Ce nom de groupe est déjà pris.', 'error')
        else:
            #print(name, file=sys.stderr)
            groupes.append({'group_name': name, 'password': password})
            session['groupe'] = name
            save_groups(groupes)
            flash('Le groupe a été créer avec succés!', 'success')
            return redirect(url_for('docs'))

    return render_template('create_group.html', mess= "Ce nom de groupe est déjà pris.")

@app.route('/connect_to_group', methods=['GET', 'POST'])
def connect_to_group():
    if request.method == 'POST':
        name = request.form['group_name']
        password = request.form['password']
    
        groupes = load_groups()
        group = next((u for u in groupes if u['group_name'] == name and u['password'] == password), None)

        if group:
            session['groupe'] = name
            return redirect(url_for('docs'))
        else:
            flash('Nom du groupe ou mot de passe incorrect.', 'error')

    return render_template('join_group.html', mess="Nom du groupe ou mot de passe incorrect.")




@app.route('/logout')
def logout():
    session.pop('username', None)  # Supprime l'utilisateur
    flash('Vous avez été déconnecté.', 'success')  
    return redirect(url_for('login'))  # Redirige vers la page de connexion

@socketio.on('text_change')
def handle_text_change(data):
    content = data.get('content')
    cursor_position = data.get('cursor_position')

    # Met à jour le stockage
    content_storage['content'] = content

    # Diffuse la mise à jour aux autres utilisateurs
    emit('update_text', {'content': content, 'cursor_position': cursor_position}, broadcast=True, include_self=False)

@socketio.on('request_initial_content')
def send_initial_content():
    print(content_storage['content'], file=sys.stderr)
    emit('update_text', {'content': content_storage['content'], 'cursor_position': None})




@socketio.on('chat')
def on_chat(data):
    user = data['user']
    group_id = data['group_id']
    join_room(group_id)  # L'utilisateur rejoint la salle spécifique au groupe

    # Ajout de l'utilisateur au groupe
    if group_id not in connected_users:
        connected_users[group_id] = set()
    connected_users[group_id].add(user)

    # Émet le nombre d'utilisateurs connectés au groupe à tous les utilisateurs du groupe
    emit('user-connected', {'count': len(connected_users[group_id])}, room=group_id)

    # Récupére les anciens messages
    messages = Message.query.filter_by(group_id=group_id).all()
    for message in messages:
        emit('message', {'user': message.user, 'message': message.message}, room=group_id)

@socketio.on('message')
def handle_message(data):
    
    user = data['user']
    group_id = data['group_id']
    message_text = data['message']

    # Sauvegarde le message dans la base de données
    new_message = Message(user=user, group_id=group_id, message=message_text)
    db.session.add(new_message)
    db.session.commit()
    
    emit('message', {'user': user, 'message': message_text}, room=group_id)



if __name__ == '__main__':
    init_db()
    socketio.run(app, host='0.0.0.0', port=5001, debug=True)
