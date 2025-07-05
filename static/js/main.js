document.addEventListener('DOMContentLoaded', () => {
    const socket = io();

    // --- Find HTML elements ---
    const timerElement = document.getElementById('timer');
    const roundIdElement = document.getElementById('round-id');
    const walletBalanceElement = document.getElementById('wallet-balance');
    const betForm = document.getElementById('bet-form');
    const betAmountInput = document.getElementById('bet-amount');
    const resultsTableBody = document.getElementById('results-table-body');

    // --- Socket.IO Listeners ---
    socket.on('timer_update', (data) => {
        const seconds = String(data.timer).padStart(2, '0');
        const newTime = `00:${seconds}`;

        if (timerElement.textContent !== newTime) {
            gsap.to(timerElement, {
                duration: 0.2, scale: 1.1,
                onComplete: () => {
                    timerElement.textContent = newTime;
                    gsap.to(timerElement, { duration: 0.4, scale: 1 });
                }
            });
        }
        
        if (data.round_id) {
            roundIdElement.textContent = `#${data.round_id}`;
        }
    });

    socket.on('new_result', (data) => {
        // Create a new row for the table
        const newRow = document.createElement('tr');
        
        // Determine badge color for the result
        let badgeClass = '';
        if (data.result_color === 'green') badgeClass = 'bg-success';
        else if (data.result_color === 'red') badgeClass = 'bg-danger';
        else badgeClass = 'bg-primary';

        // Capitalize the color name
        const colorName = data.result_color.charAt(0).toUpperCase() + data.result_color.slice(1);
        
        newRow.innerHTML = `
            <td>#${data.round_id}</td>
            <td>
                <span class="badge fs-6 ${badgeClass}">${colorName}</span>
            </td>
        `;

        // Add the new row to the top of the table
        resultsTableBody.prepend(newRow);
        
        // Animate the new row
        gsap.from(newRow, { duration: 0.5, opacity: 0, y: -20 });

        // Optional: Keep table size limited to 10 rows
        if (resultsTableBody.rows.length > 10) {
            resultsTableBody.deleteRow(-1);
        }
    });


    // --- Event Listeners ---
    betForm.addEventListener('submit', (e) => {
        e.preventDefault();

        const amount = betAmountInput.value;
        const color = e.submitter.value;

        if (!amount || amount <= 0) {
            alert('Please enter a valid amount.');
            return;
        }

        fetch('/bet', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount: amount, color: color }),
        })
        .then(response => response.json())
        .then(data => {
            alert(data.message);
            if (data.status === 'success') {
                walletBalanceElement.textContent = data.new_balance;
                betAmountInput.value = '';
            }
        })
        .catch((error) => {
            console.error('Error:', error);
            alert('An error occurred. Please try again.');
        });
    });
});