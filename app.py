import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_socketio import SocketIO
from dotenv import load_dotenv
from pymongo import MongoClient, DESCENDING
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
from functools import wraps
import time
from threading import Thread
from datetime import datetime
import random
import math

# --- Basic Setup ---
load_dotenv()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'a_fallback_secret_key')
# Serve static files for JS
app.static_folder = 'static'
socketio = SocketIO(app, async_mode='threading')

# --- Database Connection ---
try:
    mongo_uri = os.getenv('MONGO_URI')
    client = MongoClient(mongo_uri)
    db = client['xdhamaka_db']
    users_collection = db['users']
    bets_collection = db['bets']
    games_collection = db['games']
    admins_collection = db['admins']
    withdrawals_collection = db['withdrawals']
    # --- New Collections for Aviator ---
    aviator_games_collection = db['aviator_games']
    aviator_bets_collection = db['aviator_bets']
    print("✅ Successfully connected to MongoDB.")

    # Ensure admin user exists
    if admins_collection.count_documents({}) == 0:
        admins_collection.insert_one({
            'username': 'admin',
            'password': generate_password_hash('password')
        })
        print("✅ Default admin user created. Username: admin, Password: password")

except Exception as e:
    print(f"❌ Could not connect to MongoDB. Error: {e}")


# --- Game State & Settings ---
# Color Game
game_state = {
    "timer": 30,
    "round_id": None
}
BETTING_DURATION = 25
ROUND_BREAK = 5

# Aviator Game
aviator_game_state = {
    "status": "crashed", # waiting, flying, crashed
    "round_id": None,
    "crash_point": 1.0,
    "current_multiplier": 1.0,
    "start_time": None,
    "timer": 10 # Countdown for the 'waiting' phase
}
AVIATOR_WAIT_TIME = 10
AVIATOR_BREAK_TIME = 5

# --- Background Tasks ---
# --- Helper to start threads ---
thread = None
aviator_thread = None

@app.before_request
def start_background_threads():
    global thread, aviator_thread
    if thread is None:
        thread = Thread(target=game_loop)
        thread.daemon = True
        thread.start()
    if aviator_thread is None:
        aviator_thread = Thread(target=aviator_game_loop)
        aviator_thread.daemon = True
        aviator_thread.start()

# --- Color Game Loop ---
def game_loop():
    """A background thread that runs the color game loop."""
    while True:
        # 1. Start a new round
        game_state["round_id"] = datetime.now().strftime('%Y%m%d%H%M%S')
        game_state["timer"] = 30
        
        # 2. Countdown timer
        while game_state["timer"] > 0:
            socketio.emit('timer_update', {
                'timer': game_state['timer'],
                'round_id': game_state['round_id']
            })
            game_state["timer"] -= 1
            time.sleep(1)

        # 3. Determine and save the result
        colors = ["red", "green", "violet"]
        chosen_color = random.choices(colors, weights=[45, 45, 10], k=1)[0]
        
        new_game_result = {
            'round_id': game_state['round_id'],
            'result_color': chosen_color,
            'timestamp': datetime.now()
        }
        games_collection.insert_one(new_game_result)
        
        # 4. Process winning bets
        winning_bets = bets_collection.find({
            'round_id': game_state['round_id'],
            'color': chosen_color
        })

        for bet in winning_bets:
            payout_multiplier = 9 if chosen_color == "violet" else 2
            winnings = bet['amount'] * payout_multiplier
            users_collection.update_one(
                {'_id': bet['user_id']},
                {'$inc': {'wallet.balance': winnings}}
            )
            user_session_id = get_user_sid(str(bet['user_id']))
            if user_session_id:
                 socketio.emit('personal_update', {
                    'message': f"You won ₹{winnings:.2f}!",
                    'balance': users_collection.find_one({'_id': bet['user_id']})['wallet']['balance']
                }, room=user_session_id)

        # 5. Announce the result to everyone
        new_game_result['_id'] = str(new_game_result['_id'])
        new_game_result['timestamp'] = new_game_result['timestamp'].isoformat()
        socketio.emit('new_result', new_game_result)

        # 6. Break before the next round
        time.sleep(ROUND_BREAK)

# --- Aviator Game Loop ---
def generate_crash_point():
    """Generates a crash point with a skewed distribution."""
    # 50% chance for crash between 1.00 and 1.99
    if random.random() < 0.50:
        return 1.0 + random.random()
    # 30% chance for crash between 2.00 and 4.99
    if random.random() < 0.80:
        return 2.0 + random.random() * 3
    # 15% chance for crash between 5.00 and 10.00
    if random.random() < 0.95:
        return 5.0 + random.random() * 5
    # 5% chance for a high multiplier
    return 10.0 + random.random() * 15

def get_live_aviator_bets(round_id):
    """Fetches and formats live bets for the current Aviator round."""
    bets_cursor = aviator_bets_collection.find(
        {'round_id': round_id},
        {'_id': 0, 'user_id': 1, 'amount': 1, 'status': 1, 'cashout_multiplier': 1}
    )
    live_bets = []
    for bet in bets_cursor:
        user = users_collection.find_one({'_id': bet['user_id']}, {'mobile': 1})
        if user:
            # Anonymize user mobile number for privacy
            bet['user'] = f"***{user['mobile'][-4:]}"
        else:
            bet['user'] = '***????'
        del bet['user_id'] # Remove sensitive ID before sending to client
        live_bets.append(bet)
    return live_bets

def aviator_game_loop():
    """A background thread that runs the aviator game loop."""
    while True:
        # 1. WAITING phase
        aviator_game_state["status"] = "waiting"
        aviator_game_state["round_id"] = datetime.now().strftime('AV%Y%m%d%H%M%S')
        aviator_game_state["crash_point"] = generate_crash_point()
        
        for i in range(AVIATOR_WAIT_TIME, 0, -1):
            aviator_game_state["timer"] = i
            socketio.emit('aviator_state_update', {
                "status": "waiting",
                "timer": i,
                "round_id": aviator_game_state["round_id"]
            })
            # Broadcast the current list of bets
            socketio.emit('aviator_bets_update', get_live_aviator_bets(aviator_game_state["round_id"]))
            time.sleep(1)

        # 2. FLYING phase
        aviator_game_state["status"] = "flying"
        aviator_game_state["start_time"] = time.time()
        
        socketio.emit('aviator_state_update', { "status": "flying" })

        while True:
            elapsed = time.time() - aviator_game_state["start_time"]
            current_multiplier = round(1.0 + 0.05 * elapsed + 0.05 * (elapsed ** 1.5), 2)
            aviator_game_state["current_multiplier"] = current_multiplier

            if current_multiplier >= aviator_game_state["crash_point"]:
                break
            
            socketio.emit('aviator_multiplier_update', {"multiplier": current_multiplier})
            time.sleep(0.1) # Update multiplier 10 times a second

        # 3. CRASHED phase
        aviator_game_state["status"] = "crashed"
        
        final_multiplier = aviator_game_state["crash_point"]
        aviator_games_collection.insert_one({
            "round_id": aviator_game_state["round_id"],
            "crash_multiplier": final_multiplier,
            "timestamp": datetime.now()
        })
        
        # Mark non-cashed-out bets as lost
        aviator_bets_collection.update_many(
            {"round_id": aviator_game_state["round_id"], "status": "bet_placed"},
            {"$set": {"status": "lost"}}
        )

        socketio.emit('aviator_crash', {
            "multiplier": final_multiplier,
            "round_id": aviator_game_state["round_id"]
        })
        # Broadcast the final results of all bets
        socketio.emit('aviator_bets_update', get_live_aviator_bets(aviator_game_state["round_id"]))

        # 4. BREAK before next round
        time.sleep(AVIATOR_BREAK_TIME)


# --- User Session Management for SocketIO ---
user_sids = {}
@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        user_sids[session['user_id']] = request.sid
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session and session['user_id'] in user_sids:
        # A user might have multiple tabs, so we check if the sid matches
        if user_sids[session['user_id']] == request.sid:
            del user_sids[session['user_id']]
    print(f"Client disconnected: {request.sid}")

def get_user_sid(user_id):
    return user_sids.get(user_id)


# --- Decorators ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def admin_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


# --- Main User Routes ---
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('hub'))
    return render_template('index.html')

@app.route('/hub')
@login_required
def hub():
    return render_template('hub.html')

@app.route('/game')
@login_required
def game():
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    recent_games = list(games_collection.find().sort('timestamp', DESCENDING).limit(10))
    return render_template('game.html', user=user, recent_games=recent_games)

# --- NEW: Aviator Game Route ---
@app.route('/aviator')
@login_required
def aviator():
    user_id = ObjectId(session['user_id'])
    user = users_collection.find_one({'_id': user_id})
    # Find the current user's bet for the ongoing round, if any
    current_bet = None
    if aviator_game_state.get("round_id"):
      current_bet = aviator_bets_collection.find_one({
          'user_id': user_id,
          'round_id': aviator_game_state["round_id"]
      })
    if current_bet:
        current_bet['_id'] = str(current_bet['_id']) # Serialize ObjectId
        
    recent_games = list(aviator_games_collection.find().sort('timestamp', DESCENDING).limit(10))
    return render_template('aviator.html', user=user, recent_games=recent_games, current_bet=current_bet)


# --- User Auth Routes ---
@app.route('/register', methods=['POST'])
def register():
    mobile = request.form.get('mobile')
    password = request.form.get('password')

    if not mobile or not password:
        flash('Mobile and password are required.', 'error')
        return redirect(url_for('index'))

    if users_collection.find_one({'mobile': mobile}):
        flash('This mobile number is already registered.', 'error')
        return redirect(url_for('index'))

    hashed_password = generate_password_hash(password)
    new_user = {
        'mobile': mobile,
        'password': hashed_password,
        'wallet': {'balance': 100.0, 'bonus': 0.0, 'winnings': 0.0},
        'status': 'active',
        'created_at': datetime.now()
    }
    user_id = users_collection.insert_one(new_user).inserted_id
    session['user_id'] = str(user_id)
    return redirect(url_for('hub'))

@app.route('/login', methods=['POST'])
def login():
    mobile = request.form.get('mobile')
    password = request.form.get('password')
    user = users_collection.find_one({'mobile': mobile})

    if user and user.get('status') == 'blocked':
        flash('Your account has been suspended by an administrator.', 'error')
        return redirect(url_for('index'))

    if user and check_password_hash(user['password'], password):
        session['user_id'] = str(user['_id'])
        return redirect(url_for('hub'))
    else:
        flash('Invalid mobile number or password.', 'error')
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))


# --- Game and Wallet API Routes ---
@app.route('/bet', methods=['POST'])
@login_required
def place_bet():
    data = request.get_json()
    user_id = ObjectId(session['user_id'])
    
    try:
        amount = float(data.get('amount'))
        color = data.get('color')
        if color not in ['red', 'green', 'violet']:
             raise ValueError("Invalid color.")
        if amount <= 0:
            raise ValueError("Invalid amount.")
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid bet data."}), 400

    if game_state['timer'] <= (30 - BETTING_DURATION):
        return jsonify({"status": "error", "message": "Betting is closed for this round."})

    user = users_collection.find_one({'_id': user_id})
    if user['wallet']['balance'] < amount:
        return jsonify({"status": "error", "message": "Insufficient funds."})
    
    users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': -amount}})
    bets_collection.insert_one({
        'user_id': user_id, 'round_id': game_state['round_id'],
        'color': color, 'amount': amount, 'timestamp': datetime.now()
    })

    new_balance = user['wallet']['balance'] - amount
    return jsonify({
        "status": "success",
        "message": f"Bet of ₹{amount:.2f} on {color} placed!",
        "new_balance": new_balance
    })

# --- NEW: Aviator API Routes ---
@app.route('/api/aviator/bet', methods=['POST'])
@login_required
def place_aviator_bet():
    if aviator_game_state['status'] != 'waiting':
        return jsonify({"status": "error", "message": "Betting is currently closed."}), 400

    data = request.get_json()
    user_id = ObjectId(session['user_id'])
    
    try:
        amount = float(data.get('amount'))
        if amount <= 0:
            raise ValueError("Invalid bet amount.")
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid bet data."}), 400

    user = users_collection.find_one({'_id': user_id})
    if user['wallet']['balance'] < amount:
        return jsonify({"status": "error", "message": "Insufficient funds."}), 400

    # Check if user already bet on this round
    existing_bet = aviator_bets_collection.find_one({
        "user_id": user_id, "round_id": aviator_game_state['round_id']
    })
    if existing_bet:
        return jsonify({"status": "error", "message": "You have already placed a bet for this round."}), 400

    users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': -amount}})
    new_bet = {
        'user_id': user_id,
        'round_id': aviator_game_state['round_id'],
        'amount': amount,
        'status': 'bet_placed', # bet_placed, cashed_out, lost
        'timestamp': datetime.now()
    }
    aviator_bets_collection.insert_one(new_bet)

    # Broadcast updated bet list to all clients
    socketio.emit('aviator_bets_update', get_live_aviator_bets(aviator_game_state["round_id"]))

    new_balance = user['wallet']['balance'] - amount
    return jsonify({
        "status": "success",
        "message": f"Bet of ₹{amount:.2f} placed!",
        "new_balance": new_balance
    })

@app.route('/api/aviator/cancel', methods=['POST'])
@login_required
def cancel_aviator_bet():
    """Allows a user to cancel their bet during the 'waiting' phase."""
    if aviator_game_state['status'] != 'waiting':
        return jsonify({"status": "error", "message": "Cannot cancel bet now. The game has already started."}), 400
    
    user_id = ObjectId(session['user_id'])
    round_id = aviator_game_state['round_id']
    
    # Find and delete the bet in one atomic operation
    bet_to_cancel = aviator_bets_collection.find_one_and_delete({
        'user_id': user_id,
        'round_id': round_id,
        'status': 'bet_placed'
    })

    if not bet_to_cancel:
        return jsonify({"status": "error", "message": "No active bet found to cancel."}), 400
    
    # Refund the bet amount to the user's wallet
    users_collection.update_one(
        {'_id': user_id},
        {'$inc': {'wallet.balance': bet_to_cancel['amount']}}
    )
    
    # Broadcast updated bet list to all clients
    socketio.emit('aviator_bets_update', get_live_aviator_bets(round_id))
    
    user = users_collection.find_one({'_id': user_id})
    return jsonify({
        "status": "success",
        "message": "Your bet has been canceled and the amount refunded.",
        "new_balance": user['wallet']['balance']
    })

@app.route('/api/aviator/cashout', methods=['POST'])
@login_required
def cashout_aviator():
    if aviator_game_state['status'] != 'flying':
        return jsonify({"status": "error", "message": "Cannot cash out now."}), 400

    user_id = ObjectId(session['user_id'])
    round_id = aviator_game_state['round_id']
    cashout_multiplier = aviator_game_state['current_multiplier']
    
    bet_to_cashout = aviator_bets_collection.find_one({
        'user_id': user_id,
        'round_id': round_id,
        'status': 'bet_placed' # Can only cash out a placed bet
    })

    if not bet_to_cashout:
        return jsonify({"status": "error", "message": "No active bet found to cash out."}), 400
    
    winnings = bet_to_cashout['amount'] * cashout_multiplier

    # Update bet status
    aviator_bets_collection.update_one(
        {'_id': bet_to_cashout['_id']},
        {'$set': {
            'status': 'cashed_out',
            'cashout_multiplier': cashout_multiplier,
            'winnings': winnings
        }}
    )
    
    # Add winnings to user wallet
    users_collection.update_one(
        {'_id': user_id},
        {'$inc': {'wallet.balance': winnings}}
    )
    
    # Broadcast updated bet list to all clients
    socketio.emit('aviator_bets_update', get_live_aviator_bets(round_id))
    
    user = users_collection.find_one({'_id': user_id})
    
    return jsonify({
        "status": "success",
        "message": f"Cashed out at {cashout_multiplier:.2f}x for ₹{winnings:.2f}!",
        "new_balance": user['wallet']['balance']
    })


@app.route('/add_cash', methods=['POST'])
@login_required
def add_cash():
    # This route is generic and can be used from any page
    try:
        amount = float(request.form.get('amount'))
        if amount < 10:
            flash("Minimum deposit amount is ₹10.", "error")
        else:
            user_id = ObjectId(session['user_id'])
            users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': amount}})
            flash(f"Successfully added ₹{amount:.2f} to your wallet.", "success")
    except (ValueError, TypeError):
        flash("Invalid amount entered.", "error")
    
    # Redirect back to the page the user came from
    return redirect(request.referrer or url_for('hub'))


@app.route('/request_withdrawal', methods=['POST'])
@login_required
def request_withdrawal():
    # This route is also generic
    try:
        amount = float(request.form.get('amount'))
        upi_id = request.form.get('upi_id')
        if not upi_id:
            raise ValueError("UPI ID is required.")
        if amount < 100:
             raise ValueError("Minimum withdrawal amount is ₹100.")
        
        user_id = ObjectId(session['user_id'])
        user = users_collection.find_one({'_id': user_id})

        if user['wallet']['balance'] < amount:
            flash("Insufficient funds for this withdrawal.", "error")
        else:
            users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': -amount}})
            withdrawals_collection.insert_one({
                'user_id': user_id, 'amount': amount, 'upi_id': upi_id,
                'status': 'pending', 'requested_at': datetime.now()
            })
            flash("Withdrawal request submitted successfully.", "success")

    except (ValueError, TypeError) as e:
        flash(str(e) or "Invalid data entered.", "error")
    
    return redirect(request.referrer or url_for('hub'))


# --- ADMIN SECTION ---
@app.route('/admin')
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard', page='dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin = admins_collection.find_one({'username': username})

        if admin and check_password_hash(admin['password'], password):
            session['admin_id'] = str(admin['_id'])
            return redirect(url_for('admin_dashboard', page='dashboard'))
        else:
            flash('Invalid admin credentials.', 'error')
    
    return render_template('admin.html', page='login')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/<page>')
@admin_login_required
def admin_dashboard(page):
    data = {}
    if page == 'dashboard':
        data['total_users'] = users_collection.count_documents({})
        data['total_games'] = games_collection.count_documents({})
        data['total_aviator_games'] = aviator_games_collection.count_documents({})
        data['pending_withdrawals'] = withdrawals_collection.count_documents({'status': 'pending'})

    elif page == 'users':
        data['all_users'] = list(users_collection.find())

    elif page == 'game_results':
        data['game_history'] = list(games_collection.find().sort('timestamp', DESCENDING).limit(100))
    
    # --- NEW: Aviator History Page Data ---
    elif page == 'aviator_history':
        data['aviator_history'] = list(aviator_games_collection.find().sort('timestamp', DESCENDING).limit(100))

    elif page == 'withdrawals':
        pipeline = [
            {'$sort': {'requested_at': DESCENDING}},
            {'$lookup': {'from': 'users', 'localField': 'user_id', 'foreignField': '_id', 'as': 'user_details'}},
            {'$unwind': '$user_details'}
        ]
        data['requests'] = list(withdrawals_collection.aggregate(pipeline))
    
    # Use a default empty template if the partial doesn't exist
    template_to_render = f"admin_partials/{page}.html"
    if not os.path.exists(os.path.join(app.template_folder, template_to_render)):
        template_to_render = 'admin_partials/empty.html'

    return render_template('admin.html', page=page, data=data, partial_template=template_to_render)


# --- ADMIN ACTION ROUTES ---
@app.route('/admin/action/toggle_user_status/<user_id>', methods=['POST'])
@admin_login_required
def admin_toggle_user_status(user_id):
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    if user:
        new_status = 'blocked' if user.get('status', 'active') == 'active' else 'active'
        users_collection.update_one({'_id': ObjectId(user_id)}, {'$set': {'status': new_status}})
        flash(f"User status changed to {new_status}.", "success")
    return redirect(url_for('admin_dashboard', page='users'))

@app.route('/admin/action/add_bonus', methods=['POST'])
@admin_login_required
def admin_add_bonus():
    user_id = request.form.get('user_id')
    try:
        bonus_amount = float(request.form.get('bonus_amount'))
        if bonus_amount > 0:
            users_collection.update_one(
                {'_id': ObjectId(user_id)},
                {'$inc': {'wallet.balance': bonus_amount, 'wallet.bonus': bonus_amount}}
            )
            flash(f"Added ₹{bonus_amount:.2f} bonus.", "success")
    except (ValueError, TypeError):
        flash("Invalid bonus amount.", "error")
    return redirect(url_for('admin_dashboard', page='users'))

@app.route('/admin/action/process_withdrawal/<request_id>', methods=['POST'])
@admin_login_required
def admin_process_withdrawal(request_id):
    action = request.form.get('action') # 'approve' or 'reject'
    withdrawal_req = withdrawals_collection.find_one({'_id': ObjectId(request_id)})

    if not withdrawal_req:
        flash("Request not found.", "error")
        return redirect(url_for('admin_dashboard', page='withdrawals'))

    if action == 'approve':
        withdrawals_collection.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': {'status': 'approved', 'processed_at': datetime.now()}}
        )
        flash("Withdrawal approved.", "success")
    elif action == 'reject':
        withdrawals_collection.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': {'status': 'rejected', 'processed_at': datetime.now()}}
        )
        users_collection.update_one(
            {'_id': withdrawal_req['user_id']},
            {'$inc': {'wallet.balance': withdrawal_req['amount']}}
        )
        flash("Withdrawal rejected and amount refunded to user.", "warning")

    return redirect(url_for('admin_dashboard', page='withdrawals'))


# --- Main Execution ---
if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)