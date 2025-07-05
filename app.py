import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_socketio import SocketIO
from dotenv import load_dotenv
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
from functools import wraps
import time
from threading import Thread
from datetime import datetime
import random # New import for random result

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
socketio = SocketIO(app)

# --- Database Connection ---
mongo_uri = os.getenv('MONGO_URI')
client = MongoClient(mongo_uri)
db = client['9xdhamaka']
users_collection = db['users']
bets_collection = db['bets']
games_collection = db['games'] # New collection for game history
admins_collection = db['admins']
withdrawals_collection = db['withdrawals']

print("✅ Successfully connected to MongoDB.")


# --- Game State & Settings ---
game_state = {
    "timer": 30, # Shortened for easier testing
    "round_id": None
}
BETTING_DURATION = 25 # Bets are allowed for the first 25 seconds

# --- Background Task ---
def game_loop():
    """A background thread that runs the game loop."""
    while True:
        # --- Betting Phase ---
        if game_state["round_id"] is None:
            game_state["round_id"] = datetime.now().strftime('%Y%m%d%H%M%S')
            game_state["timer"] = 30 # Reset timer

        current_round_id = game_state["round_id"]
        
        while game_state["timer"] > 0:
            socketio.emit('timer_update', {
                'timer': game_state['timer'],
                'round_id': current_round_id
            })
            game_state["timer"] -= 1
            time.sleep(1)

        # --- Result Processing Phase ---
        colors = ["red", "green", "violet"]
        chosen_color = random.choices(colors, weights=[45, 45, 10], k=1)[0]
        
        games_collection.insert_one({
            'round_id': current_round_id,
            'result_color': chosen_color,
            'timestamp': datetime.now()
        })
        
        winning_bets = bets_collection.find({
            'round_id': current_round_id,
            'color': chosen_color
        })

        for bet in winning_bets:
            payout_multiplier = 1.5 if chosen_color == "violet" else 2
            winnings = bet['amount'] * payout_multiplier
            
            users_collection.update_one(
                {'_id': bet['user_id']},
                {'$inc': {'wallet.balance': winnings}}
            )
        
        print(f"Round {current_round_id} ended. Result: {chosen_color}. Processed payouts.")
        
        socketio.emit('new_result', {
            'round_id': current_round_id,
            'result_color': chosen_color
        })

        time.sleep(5) 
        game_state["round_id"] = None


# Start the background thread once the first request comes in
thread = None

@app.before_request
def start_game_loop():
    global thread
    if thread is None:
        thread = Thread(target=game_loop)
        thread.daemon = True
        thread.start()


# --- Decorators ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json:
                return jsonify({"status": "error", "message": "Login required"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_user' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


# --- Routes ---
@app.route('/')
@login_required
def index():
    user_id_str = session.get('user_id')
    user = users_collection.find_one({'_id': ObjectId(user_id_str)})
    recent_games = games_collection.find().sort('timestamp', -1).limit(10)
    return render_template('index.html', user=user, recent_games=list(recent_games))

@app.route('/bet', methods=['POST'])
@login_required
def place_bet():
    data = request.get_json()
    amount = int(data.get('amount'))
    color = data.get('color')
    user_id = ObjectId(session.get('user_id'))

    if game_state['timer'] <= (30 - BETTING_DURATION):
        return jsonify({"status": "error", "message": "Betting is closed for this round."})
    
    if not amount or amount <= 0:
        return jsonify({"status": "error", "message": "Invalid bet amount."})

    user = users_collection.find_one({'_id': user_id})
    if user['wallet']['balance'] < amount:
        return jsonify({"status": "error", "message": "Insufficient funds."})
    
    users_collection.update_one(
        {'_id': user_id},
        {'$inc': {'wallet.balance': -amount}}
    )

    bets_collection.insert_one({
        'user_id': user_id,
        'round_id': game_state['round_id'],
        'color': color,
        'amount': amount,
        'timestamp': datetime.now()
    })

    new_balance = user['wallet']['balance'] - amount
    return jsonify({
        "status": "success", 
        "message": f"Bet of ₹{amount} on {color} placed!",
        "new_balance": new_balance
    })

# --- Dummy Payment Routes ---
@app.route('/add_cash')
@login_required
def add_cash_page():
    return render_template('add_cash.html')

@app.route('/dummy_payment', methods=['POST'])
@login_required
def dummy_payment():
    amount = request.form.get('amount')
    return render_template('dummy_payment.html', amount=amount)

@app.route('/process_dummy_payment', methods=['POST'])
@login_required
def process_dummy_payment():
    status = request.form.get('status')
    
    if status == 'success':
        amount = float(request.form.get('amount'))
        user_id = ObjectId(session.get('user_id'))
        
        users_collection.update_one(
            {'_id': user_id},
            {'$inc': {'wallet.balance': amount}}
        )
        flash(f'Successfully added ₹{amount} to your wallet!', 'success')
    else:
        flash('The payment failed. Please try again.', 'danger')

    return redirect(url_for('index'))

# --- Withdrawal Routes for User ---
@app.route('/withdraw')
@login_required
def withdraw_page():
    user_id = ObjectId(session.get('user_id'))
    user = users_collection.find_one({'_id': user_id})
    return render_template('withdraw.html', user=user)

@app.route('/request_withdrawal', methods=['POST'])
@login_required
def request_withdrawal():
    user_id = ObjectId(session.get('user_id'))
    user = users_collection.find_one({'_id': user_id})
    
    try:
        amount = float(request.form.get('amount'))
        upi_id = request.form.get('upi_id')

        if not upi_id:
            flash("UPI ID is required.", "danger")
            return redirect(url_for('withdraw_page'))

        if amount <= 0:
            flash("Amount must be positive.", "danger")
            return redirect(url_for('withdraw_page'))
        
        if user['wallet']['balance'] < amount:
            flash("Insufficient balance for withdrawal.", "danger")
            return redirect(url_for('withdraw_page'))

        # Deduct amount from user's wallet
        users_collection.update_one(
            {'_id': user_id},
            {'$inc': {'wallet.balance': -amount}}
        )

        # Create a withdrawal request record
        withdrawals_collection.insert_one({
            'user_id': user_id,
            'amount': amount,
            'upi_id': upi_id,
            'status': 'pending', # Statuses: pending, approved, rejected
            'requested_at': datetime.now()
        })
        
        flash("Withdrawal request submitted successfully!", "success")
        return redirect(url_for('index'))

    except (ValueError, TypeError):
        flash("Invalid amount entered.", "danger")
        return redirect(url_for('withdraw_page'))

# --- Admin Routes ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        admin = admins_collection.find_one({'username': username})
        
        if admin and check_password_hash(admin['password'], password):
            session['admin_user'] = admin['username']
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid admin credentials.', 'danger')
            return redirect(url_for('admin_login'))
            
    return render_template('admin/login.html')

@app.route('/admin/dashboard')
@admin_login_required
def admin_dashboard():
    return render_template('admin/layout.html', title="Dashboard")

@app.route('/admin/users')
@admin_login_required
def admin_users():
    all_users = list(users_collection.find())
    return render_template('admin/users.html', all_users=all_users)

@app.route('/admin/toggle_user_status/<user_id>', methods=['POST'])
@admin_login_required
def admin_toggle_user_status(user_id):
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    if user:
        # Toggle the status
        new_status = 'active' if user.get('status') == 'blocked' else 'blocked'
        users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'status': new_status}}
        )
    return redirect(url_for('admin_users'))

@app.route('/admin/add_bonus', methods=['POST'])
@admin_login_required
def admin_add_bonus():
    user_id = request.form.get('user_id')
    try:
        bonus_amount = float(request.form.get('bonus_amount'))
        if bonus_amount > 0:
            users_collection.update_one(
                {'_id': ObjectId(user_id)},
                {'$inc': {'wallet.bonus': bonus_amount}}
            )
            flash(f"Successfully added ₹{bonus_amount} bonus.", "success")
        else:
            flash("Bonus amount must be positive.", "danger")
    except (ValueError, TypeError):
        flash("Invalid bonus amount.", "danger")
        
    return redirect(url_for('admin_users'))

@app.route('/admin/game_results')
@admin_login_required
def admin_game_results():
    # Fetch all games, sorted by newest first
    game_history = list(games_collection.find().sort('timestamp', -1))
    return render_template('admin/game_results.html', game_history=game_history)

@app.route('/admin/withdrawals')
@admin_login_required
def admin_withdrawals():
    # We need to fetch requests and enrich them with user details
    requests_cursor = withdrawals_collection.find().sort('requested_at', -1)
    enriched_requests = []
    for req in requests_cursor:
        user_info = users_collection.find_one({'_id': req['user_id']})
        if user_info:
            req['user_details'] = user_info
        else:
            req['user_details'] = {'mobile': 'N/A'}
        enriched_requests.append(req)
        
    return render_template('admin/withdrawals.html', requests=enriched_requests)

@app.route('/admin/process_withdrawal/<request_id>', methods=['POST'])
@admin_login_required
def admin_process_withdrawal(request_id):
    action = request.form.get('action')
    req = withdrawals_collection.find_one({'_id': ObjectId(request_id)})

    if not req:
        flash("Request not found.", "danger")
        return redirect(url_for('admin_withdrawals'))

    if req['status'] != 'pending':
        flash(f"This request has already been {req['status']}.", "warning")
        return redirect(url_for('admin_withdrawals'))

    if action == 'approve':
        withdrawals_collection.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': {'status': 'approved', 'processed_at': datetime.now()}}
        )
        flash("Withdrawal approved.", "success")
        
    elif action == 'reject':
        # Refund the money to the user's wallet
        users_collection.update_one(
            {'_id': req['user_id']},
            {'$inc': {'wallet.balance': req['amount']}}
        )
        # Update the request status
        withdrawals_collection.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': {'status': 'rejected', 'processed_at': datetime.now()}}
        )
        flash("Withdrawal rejected and funds returned to user.", "success")
        
    return redirect(url_for('admin_withdrawals'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_user', None)
    return redirect(url_for('admin_login'))

# --- User Auth Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        mobile = request.form.get('mobile')
        password = request.form.get('password')
        if not mobile or not password:
            flash('Mobile and password are required.', 'danger')
            return redirect(url_for('register'))
        existing_user = users_collection.find_one({'mobile': mobile})
        if existing_user:
            flash('Mobile number already registered.', 'danger')
            return redirect(url_for('register'))
        hashed_password = generate_password_hash(password)
        users_collection.insert_one({
            'mobile': mobile,
            'password': hashed_password,
            'wallet': {'balance': 1000, 'bonus': 0, 'winnings': 0},
            'status': 'active' # Add this line
        })
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        mobile = request.form.get('mobile')
        password = request.form.get('password')
        user = users_collection.find_one({'mobile': mobile})
        
        # Add this check for blocked status
        if user and user.get('status') == 'blocked':
            flash('Your account has been suspended.', 'danger')
            return redirect(url_for('login'))

        if user and check_password_hash(user['password'], password):
            session['user_id'] = str(user['_id'])
            return redirect(url_for('index'))
        else:
            flash('Invalid mobile number or password.', 'danger')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

# --- Main Execution ---
if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
