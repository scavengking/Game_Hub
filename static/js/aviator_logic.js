document.addEventListener('DOMContentLoaded', () => {
    const socket = io();

    // --- Global & Game Elements ---
    const walletBalanceElement = document.getElementById('wallet-balance');
    const notificationContainer = document.getElementById('notification-container');
    const plane = document.getElementById('plane');
    const gameScreen = document.getElementById('game-screen');
    const multiplierDisplay = document.getElementById('multiplier-display');
    const gameStateOverlay = document.getElementById('game-state-overlay');
    const gameStateMessage = document.getElementById('game-state-message');
    const gameStateTimer = document.getElementById('game-state-timer');
    const betButton = document.getElementById('bet-button');
    const betAmountInput = document.getElementById('bet-amount');
    const currentBetDisplay = document.getElementById('current-bet-display');
    const liveBetsContainer = document.getElementById('live-bets-container');

    // --- Canvas for Flight Path ---
    const canvas = document.getElementById('flight-path');
    const ctx = canvas.getContext('2d');
    let pathPoints = [];
    let planeAnimation;

    let currentMultiplier = 1.0;
    let userBetState = 'idle'; // idle, placing, bet_placed, canceling, cashing_out, cashed_out, lost
    let gameState = 'loading';

    // --- Resizing Canvas ---
    const resizeCanvas = () => {
        canvas.width = gameScreen.clientWidth;
        canvas.height = gameScreen.clientHeight;
    };
    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();

    // --- Notification System ---
    window.showNotification = (message, type = 'info') => {
        if (!notificationContainer) return;
        let bgColor, icon;
        switch (type) {
            case 'success': bgColor = 'bg-green-500/80'; icon = '✓'; break;
            case 'error': bgColor = 'bg-red-500/80'; icon = '✖'; break;
            default: bgColor = 'bg-violet-500/80'; icon = 'ℹ'; break;
        }
        const notification = document.createElement('div');
        notification.className = `flex items-center gap-4 ${bgColor} text-white p-4 rounded-lg shadow-lg transform transition-all duration-300 ease-in-out translate-x-full opacity-0`;
        notification.innerHTML = `<p class="font-bold text-xl">${icon}</p><p class="font-medium">${message}</p>`;
        notificationContainer.appendChild(notification);
        gsap.to(notification, { x: 0, opacity: 1, duration: 0.5, ease: 'power2.out' });
        setTimeout(() => {
            gsap.to(notification, { x: '100%', opacity: 0, duration: 0.5, ease: 'power2.in', onComplete: () => notification.remove() });
        }, 4000);
    };

    // --- UI Update Functions ---
    const updateBetButton = (state, text) => {
        betButton.disabled = false;
        betButton.className = 'w-full text-white font-bold py-4 px-4 rounded-lg transition duration-300 text-xl'; // Reset classes
        switch (state) {
            case 'bet':
                betButton.classList.add('bg-green-600', 'hover:bg-green-700');
                betButton.textContent = text || 'Place Bet';
                break;
            case 'cancel':
                betButton.classList.add('bg-red-600', 'hover:bg-red-700');
                betButton.textContent = text || 'Cancel Bet';
                break;
            case 'cashout':
                betButton.classList.add('bg-yellow-500', 'hover:bg-yellow-600');
                betButton.textContent = text || `Cash Out`;
                break;
            case 'disabled':
                betButton.classList.add('bg-gray-500', 'cursor-not-allowed', 'opacity-50');
                betButton.textContent = text || '...';
                betButton.disabled = true;
                break;
        }
    };
    
    const updateCurrentBetDisplay = (state, amount, cashoutMultiplier) => {
        let content = '';
        switch(state) {
            case 'bet_placed': content = `<p>Bet Placed: <span class="font-bold text-white">₹${amount.toFixed(2)}</span></p>`; break;
            case 'cashed_out':
                const winnings = amount * cashoutMultiplier;
                content = `<p class="text-green-400">Cashed Out @ <span class="font-bold">${cashoutMultiplier.toFixed(2)}x</span></p><p class="font-bold text-white">Won: ₹${winnings.toFixed(2)}</p>`;
                break;
            case 'lost': content = `<p class="text-red-400">Lost Bet: <span class="font-bold">₹${amount.toFixed(2)}</span></p>`; break;
            default: content = `<p class="text-gray-400">No bet placed for this round.</p>`; break;
        }
        currentBetDisplay.innerHTML = content;
    };

    // --- Socket.IO Event Handlers ---
    socket.on('aviator_state_update', (data) => {
        gameState = data.status;
        switch (data.status) {
            case 'waiting':
                userBetState = 'idle';
                if (planeAnimation) planeAnimation.kill();
                pathPoints = [];
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                gsap.to(plane, { x: 0, y: 0, rotation: 0, duration: 0.5, ease: 'power2.out' });
                gsap.to(multiplierDisplay, { opacity: 0, duration: 0.3 });
                gsap.to(gameStateOverlay, { opacity: 1, duration: 0.3 });
                gameStateMessage.textContent = "Waiting for next round...";
                gameStateTimer.textContent = data.timer;
                updateBetButton('bet');
                betAmountInput.disabled = false;
                updateCurrentBetDisplay('idle');
                break;

            case 'flying':
                gsap.to(gameStateOverlay, { opacity: 0, duration: 0.5 });
                gsap.to(multiplierDisplay, { opacity: 1, duration: 0.5, delay: 0.3 });
                multiplierDisplay.textContent = '1.00x';
                betAmountInput.disabled = true;
                if(userBetState === 'bet_placed'){ updateBetButton('cashout'); } 
                else { updateBetButton('disabled', 'Betting Closed'); }
                
                planeAnimation = gsap.to(plane, {
                    x: canvas.width * 0.8,
                    y: -canvas.height * 0.7,
                    rotation: -15,
                    duration: 30,
                    ease: "power1.inOut",
                    onUpdate: () => {
                        const planeRect = plane.getBoundingClientRect();
                        const screenRect = gameScreen.getBoundingClientRect();
                        pathPoints.push({ x: planeRect.left - screenRect.left + planeRect.width / 2, y: planeRect.top - screenRect.top + planeRect.height / 2 });
                        drawPath();
                    }
                });
                break;
        }
    });

    socket.on('aviator_multiplier_update', (data) => {
        currentMultiplier = data.multiplier;
        multiplierDisplay.textContent = `${currentMultiplier.toFixed(2)}x`;
        if (userBetState === 'bet_placed') {
            const potentialWinnings = parseFloat(betAmountInput.value) * currentMultiplier;
            updateBetButton('cashout', `Cash Out (₹${potentialWinnings.toFixed(2)})`);
        }
    });

    socket.on('aviator_crash', (data) => {
        gameState = 'crashed';
        if (planeAnimation) planeAnimation.kill();
        if (userBetState === 'bet_placed') {
            userBetState = 'lost';
            updateCurrentBetDisplay('lost', parseFloat(betAmountInput.value));
            showNotification(`Flew away! You lost your bet.`, 'error');
        }

        gsap.to(plane, { x: '120vw', y: '-80vh', opacity: 0, duration: 0.5, ease: 'power1.in' });
        gsap.to(gameScreen, { backgroundColor: '#4c1d24', duration: 0.1, yoyo: true, repeat: 3 });
        gsap.to(gameStateOverlay, { opacity: 1, duration: 0.3, delay: 0.5 });
        
        multiplierDisplay.classList.add('text-red-500');
        gsap.fromTo(multiplierDisplay, {scale: 1}, {scale: 1.1, duration: 0.1, yoyo: true, repeat: 3});
        
        gameStateMessage.textContent = `Flew Away @ ${data.multiplier.toFixed(2)}x`;
        gameStateTimer.textContent = "";
        updateBetButton('disabled', 'Round Over');
        
        setTimeout(() => {
            gsap.to(plane, { x: 0, y: 0, opacity: 1, duration: 0 });
            multiplierDisplay.classList.remove('text-red-500');
            setTimeout(() => window.location.reload(), 3000);
        }, 2000);
    });

    socket.on('aviator_bets_update', (bets) => {
        liveBetsContainer.innerHTML = '';
        if (bets.length === 0) {
            liveBetsContainer.innerHTML = '<p class="text-center text-gray-500">No bets placed yet for this round.</p>';
            return;
        }
        bets.forEach(bet => {
            let statusClass = 'bg-gray-800/50';
            let statusText = `₹${bet.amount.toFixed(2)}`;
            if (bet.status === 'cashed_out') {
                statusClass = 'bg-green-500/20';
                statusText = `<span class="font-bold text-green-300">₹${(bet.amount * bet.cashout_multiplier).toFixed(2)} @ ${bet.cashout_multiplier.toFixed(2)}x</span>`;
            } else if (bet.status === 'lost') {
                statusClass = 'bg-red-500/20 text-red-400 line-through';
            }
            const betElement = document.createElement('div');
            betElement.className = `flex justify-between items-center p-2 rounded-lg ${statusClass}`;
            betElement.innerHTML = `<span class="text-gray-300">${bet.user}</span> <span class="text-sm">${statusText}</span>`;
            liveBetsContainer.appendChild(betElement);
        });
    });

    // --- Main Button Logic ---
    betButton.addEventListener('click', () => {
        if (userBetState === 'idle' && gameState === 'waiting') { // Place Bet
            const amount = betAmountInput.value;
            if (!amount || amount <= 0) { showNotification('Please enter a valid bet amount.', 'error'); return; }
            updateBetButton('disabled', 'Placing...');
            userBetState = 'placing';
            fetch('/api/aviator/bet', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ amount }) })
            .then(res => res.json()).then(data => {
                if (data.status === 'success') {
                    showNotification(data.message, 'success');
                    walletBalanceElement.textContent = parseFloat(data.new_balance).toFixed(2);
                    userBetState = 'bet_placed';
                    updateBetButton('cancel');
                    updateCurrentBetDisplay('bet_placed', parseFloat(amount));
                } else { showNotification(data.message, 'error'); updateBetButton('bet'); userBetState = 'idle'; }
            });
        } else if (userBetState === 'bet_placed' && gameState === 'waiting') { // Cancel Bet
            updateBetButton('disabled', 'Canceling...');
            userBetState = 'canceling';
            fetch('/api/aviator/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
            .then(res => res.json()).then(data => {
                if (data.status === 'success') {
                    showNotification(data.message, 'success');
                    walletBalanceElement.textContent = parseFloat(data.new_balance).toFixed(2);
                    userBetState = 'idle';
                    updateBetButton('bet');
                    updateCurrentBetDisplay('idle');
                } else { showNotification(data.message, 'error'); updateBetButton('cancel'); userBetState = 'bet_placed'; }
            });
        } else if (userBetState === 'bet_placed' && gameState === 'flying') { // Cash Out
            updateBetButton('disabled', 'Cashing Out...');
            userBetState = 'cashing_out';
            fetch('/api/aviator/cashout', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
            .then(res => res.json()).then(data => {
                 if (data.status === 'success') {
                    showNotification(data.message, 'success');
                    walletBalanceElement.textContent = parseFloat(data.new_balance).toFixed(2);
                    userBetState = 'cashed_out';
                    updateBetButton('disabled', 'Cashed Out!');
                    updateCurrentBetDisplay('cashed_out', parseFloat(betAmountInput.value), currentMultiplier);
                 } else { showNotification(data.message, 'error'); if (gameState === 'flying') { updateBetButton('cashout'); userBetState = 'bet_placed'; } }
            });
        }
    });

    // --- Canvas Drawing Function ---
    function drawPath() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (pathPoints.length < 2) return;
        ctx.beginPath();
        ctx.moveTo(pathPoints[0].x, pathPoints[0].y);
        for (let i = 1; i < pathPoints.length; i++) {
            ctx.lineTo(pathPoints[i].x, pathPoints[i].y);
        }
        const gradient = ctx.createLinearGradient(0, canvas.height, 0, 0);
        gradient.addColorStop(0, "rgba(253, 224, 71, 0.5)");
        gradient.addColorStop(1, "rgba(250, 204, 21, 0)");
        ctx.strokeStyle = gradient;
        ctx.lineWidth = 3;
        ctx.stroke();
    }
    
    // --- Modal Logic (Unchanged) ---
    const allModals = document.querySelectorAll('.modal-backdrop');
    const openModal = (modalId) => document.getElementById(modalId)?.classList.remove('modal-hidden');
    const closeModal = (modal) => modal.classList.add('modal-hidden');
    document.getElementById('add-cash-btn')?.addEventListener('click', () => openModal('add-cash-modal'));
    document.getElementById('withdraw-btn')?.addEventListener('click', () => openModal('withdraw-modal'));
    allModals.forEach(modal => {
        modal.querySelector('.close-modal-btn')?.addEventListener('click', () => closeModal(modal));
        modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(modal); });
    });

    // --- Initial state from server-rendered data ---
    if (typeof current_bet !== 'undefined' && current_bet) {
         userBetState = current_bet.status;
         betAmountInput.value = current_bet.amount;
         betAmountInput.disabled = true;
        if (current_bet.status === 'bet_placed') {
            updateCurrentBetDisplay('bet_placed', current_bet.amount);
            updateBetButton('cancel');
        } else if (current_bet.status === 'cashed_out') {
             updateCurrentBetDisplay('cashed_out', current_bet.amount, current_bet.cashout_multiplier);
             updateBetButton('disabled', 'Cashed Out');
        }
    }
});