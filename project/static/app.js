const socket = io();

/* ============================= */
/* MOUSE GLOW */
/* ============================= */

const mouseGlow =
    document.getElementById(
        'mouseGlow'
    );

document.addEventListener(
    'mousemove',
    (e)=>{

        mouseGlow.style.left =
            e.clientX + 'px';

        mouseGlow.style.top =
            e.clientY + 'px';

    }
);

/* ============================= */
/* PARTICLES */
/* ============================= */

function createParticles(){

    const layer =
        document.getElementById(
            'particleLayer'
        );

    for(let i=0;i<120;i++){

        const p =
            document.createElement('div');

        p.className = 'particle';

        p.style.left =
            Math.random()*100 + '%';

        p.style.animationDuration =
            (10 + Math.random()*30) + 's';

        p.style.animationDelay =
            Math.random()*20 + 's';

        layer.appendChild(p);

    }

}

/* ============================= */
/* FLOATING SYMBOLS */
/* ============================= */

function createSymbols(){

    const symbols = [
        'EURUSD',
        'GBPUSD',
        'USDJPY',
        'XAUUSD',
        'BTCUSD',
        'NASDAQ',
        'SPX500'
    ];

    const layer =
        document.getElementById(
            'symbolLayer'
        );

    for(let i=0;i<35;i++){

        const s =
            document.createElement('div');

        s.className = 'symbol';

        s.innerHTML =
            symbols[
                Math.floor(
                    Math.random()
                    * symbols.length
                )
            ];

        s.style.left =
            Math.random()*100 + '%';

        s.style.animationDuration =
            (20 + Math.random()*25)
            + 's';

        s.style.animationDelay =
            Math.random()*20 + 's';

        layer.appendChild(s);

    }

}

/* ============================= */
/* FLOATING MONEY */
/* ============================= */

function createBanknotes(){

    const currencies = [
        'usd',
        'eur',
        'gbp',
        'jpy',
        'aud',
        'cad',
        'chf'
    ];

    const container =
        document.getElementById(
            'banknoteBg'
        );

    for(let i=0;i<45;i++){

        const img =
            document.createElement('img');

        const currency =
            currencies[
                Math.floor(
                    Math.random()
                    * currencies.length
                )
            ];

        img.src =
            `/static/money/${currency}.png`;

        img.className =
            'banknote';

        img.style.left =
            Math.random()*100 + '%';

        img.style.width =
            (120 + Math.random()*180)
            + 'px';

        img.style.animationDuration =
            (18 + Math.random()*35)
            + 's';

        img.style.animationDelay =
            Math.random()*20 + 's';

        img.style.opacity =
            0.04 + Math.random()*0.08;

        container.appendChild(img);

    }

}

/* ============================= */
/* RADAR CHART */
/* ============================= */

const radarCtx =
    document.getElementById(
        'radarChart'
    );

const radarChart =
    new Chart(radarCtx, {

        type:'radar',

        data:{

            labels:[
                'Technical',
                'Volume',
                'Momentum',
                'Fundamental',
                'Sentiment'
            ],

            datasets:[{

                label:'AI Confidence',

                data:[50,50,50,50,50],

                borderColor:'#00ffff',

                backgroundColor:
                    'rgba(0,255,255,0.15)'

            }]

        }

    });

/* ============================= */
/* SOCKET */
/* ============================= */

socket.on(
    'dashboard_update',
    (data)=>{

        document.getElementById(
            'balance'
        ).innerHTML =
            '$' +
            data.balance.toFixed(2);

        document.getElementById(
            'equity'
        ).innerHTML =
            '$' +
            data.equity.toFixed(2);

        document.getElementById(
            'confidence'
        ).innerHTML =
            data.confidence.overall
            + '%';

        radarChart.data.datasets[0]
        .data = [

            data.confidence.technical,
            data.confidence.volume,
            data.confidence.momentum,
            data.confidence.fundamental,
            data.confidence.sentiment

        ];

        radarChart.update();

        document.getElementById(
            'positionsCount'
        ).innerHTML =
            data.positions.length;

        let html = '';

        data.positions.forEach(pos=>{

            html += `

            <tr>

                <td>${pos.symbol}</td>

                <td>${pos.type}</td>

                <td>${pos.volume}</td>

                <td>${pos.profit.toFixed(2)}</td>

            </tr>

            `;

        });

        document.getElementById(
            'positionsTable'
        ).innerHTML = html;

    }
);

/* ============================= */
/* ORDER EXECUTION */
/* ============================= */

function sendOrder(action){

    fetch('/api/order',{

        method:'POST',

        headers:{
            'Content-Type':
                'application/json'
        },

        body:JSON.stringify({

            symbol:
                document.getElementById(
                    'symbol'
                ).value,

            volume:
                document.getElementById(
                    'volume'
                ).value,

            action:action

        })

    })

    .then(r=>r.json())

    .then(data=>{

        if(data.success){

            alert(
                'Order Placed: '
                + data.ticket
            );

        }else{

            alert(
                'Error: '
                + data.error
            );

        }

    });

}

/* ============================= */
/* START EFFECTS */
/* ============================= */

createParticles();

createSymbols();

createBanknotes();