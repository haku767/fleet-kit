// The fantasy look: the fleet as a game. The day's quest (the most items open today is its
// size, what went since is done; a streak of days the list reached zero; confetti when this
// browser watches it reach zero), mana (every bot's daily budget, emptied by what it spends),
// the bots as figures with sprites and their level on the shoulder, XP, levels and badges per
// project and bot, history cards, favourites pinned under the quest. The numbers every browser
// must agree on (quest, levels, badges, the fleet's streak) come from the set's collector half
// in d.sets.fantasy; this file only draws them. Favourites and a budget override are this
// browser's (localStorage).
(() => {
  const F = window.fleet;
  const {esc, tag, ago, when, money, who, plink, blink, sum, last, store, todo, actions, itemHtml, hist, hp, hb, spark,
         botState, figure, stateWord, botFigure, pipelineBar, projectBots, projectChips, slimLine, prsTable, issuesTable,
         botCard, hero, roundsTable, guard, machine, guardsLine, activeBots, withRounds, nameOf} = F;
  const block = d => (d.sets || {}).fantasy || {};
  const lvOf = (d, kind, name) => ((block(d).levels || {})[kind] || {})[name] || null;
  const RANKS = ["wood", "bronze", "silver", "gold", "platinum", "diamond"];
  const XP = () => block(data()).xp || {merged: 10, closed: 3, done: 2};
  const data = () => F.data;

  // ---- favourites and budgets: this browser's ----
  const favs = () => store.get("fleet.favs", []);
  const isFav = name => favs().includes(name);
  const toggleFav = name => store.set("fleet.favs", isFav(name) ? favs().filter(f => f !== name) : [...favs(), name]);
  const starBtn = name => `<button class="star${isFav(name) ? " on" : ""}" data-fav="${esc(name)}" title="${isFav(name) ? "unpin" : "pin under the quest"}">★</button>`;
  const budgetOf = (d, b) => { const v = Number(store.get(`fleet.budget.${b.name}`, NaN)); if (Number.isFinite(v) && v >= 0) return v;
    const bg = block(d).budgets || {}; return b.kind === "dev" ? (bg.dev ?? 2) : b.kind === "plan" ? (bg.plan ?? 1) : 0; };
  const manaOf = (d, b) => { const budget = budgetOf(d, b), spent = b.cost_today || 0; const left = Math.max(0, budget - spent);
    return {budget, spent, left, pct: budget ? Math.round(left / budget * 100) : 0}; };
  const LOW_MANA = 10;
  const manaBar = (m, thin) => `<div class="bar mana${m.pct <= LOW_MANA ? " low" : ""}${thin ? " thin" : ""}" title="${m.pct} % left"><i style="width:${m.pct}%"></i></div>`;

  // ---- levels: drawn from the collector half's numbers ----
  const levelFloor = lv => 20 * lv * lv;
  const lvTag = (x, small) => `<span class="lv${small ? " s" : ""}" title="${x.xp} XP, level ${x.lv + 1} at ${levelFloor(x.lv + 1)}">Lv ${x.lv}</span>`;
  const xpBar = (x, thin) => { const a = levelFloor(x.lv), b = levelFloor(x.lv + 1), pct = Math.round((x.xp - a) / (b - a) * 100);
    return `<div class="bar xp${thin ? " thin" : ""}" title="${x.xp - a} of ${b - a} XP to level ${x.lv + 1}"><i style="width:${pct}%"></i></div>`; };
  const badges = list => `<div class="badges">${(list || []).map(b => `<span class="tag ${b.r < 0 ? "off" : "rk" + b.r}" title="${esc(b.why)}"><b>${esc(b.name)}</b><small>${b.r < 0 ? "no rank" : RANKS[b.r]}</small></span>`).join("")}</div>`;
  const shoulder = (d, b) => { const x = lvOf(d, "bots", b.name); return x ? `<span class="lvb" title="level ${x.lv}: ${x.xp} XP, level ${x.lv + 1} at ${levelFloor(x.lv + 1)}">${x.lv}</span>` : ""; };
  // "Lv 7 ▬▬▬ 1 240 XP ▂▃▁▅ merges, 14 days"
  const levelLine = (d, p) => { const x = lvOf(d, "projects", p.name), h = hp(d, p.name); if (!x || !h) return "";
    return `<div class="lvl">${lvTag(x)}${xpBar(x, true)}<span>${x.xp} XP</span><span title="merged pull requests per day, 14 days">${spark(last(h.merged, 14), "", last(hist(d).days, 14))}</span></div>`; };

  // ---- the quest and the mana ----
  function confetti() {
    const colors = ["#5fb57a", "#e06c75", "#e5c07b", "#61afef", "#c678dd"];
    for (let i = 0; i < 80; i++) {
      const e = document.createElement("i"); e.className = "confetti";
      e.style.left = Math.random() * 100 + "vw"; e.style.background = colors[i % colors.length];
      e.style.animationDelay = Math.random() * 0.8 + "s"; e.style.animationDuration = 2 + Math.random() * 1.5 + "s";
      document.body.appendChild(e); setTimeout(() => e.remove(), 4500);
    }
  }
  const DONE_SHOWN = 8;
  function doneList(d, done) {
    if (!done || !done.length) return "";
    const li = x => `<li><span title="on the list since ${esc(when(x.since))}">${esc(when(x.at))}</span> ${tag(x.tag || x.kind || "done", x.cls || "")} ${itemHtml(d, {...x, since: null})}<span class="p">${esc(x.project || "")}</span></li>`;
    return `<ul class="donelist">${done.slice(0, DONE_SHOWN).map(li).join("")}${done.length > DONE_SHOWN ? `<li style="padding-left:0;text-indent:0">… and ${done.length - DONE_SHOWN} more</li>` : ""}</ul>`;
  }
  const fleetLine = d => { const f = block(d).fleet_streak, h = hist(d); if (!f || !h.days.length) return "";
    const txt = f.n ? `⚔ fleet: ${f.capped ? f.n + "+" : f.n} day${f.n > 1 ? "s" : ""} in a row with a merge${f.today_has ? "" : " · none yet today"}` : `⚔ fleet: no merge yesterday or today`;
    return `<div class="dim" title="days in a row on which something was merged somewhere in the fleet, counted by the collector">${txt} ${spark(last(f.per, 14), "", last(h.days, 14))}</div>`; };
  function quest(d, open) {
    const q = block(d).quest;
    const prev = store.get("fleet.open", null); store.set("fleet.open", open);
    if (prev > 0 && open === 0) confetti();
    if (!q) return `<div class="card"><h3>Today's quest</h3><div class="err">${esc(block(d).error || "this fleet.json has no fantasy block: is the fantasy set in page.sets?")}</div></div>`;
    const max = Math.max(q.max || 0, open), done = max - open, pct = max ? Math.round(done / max * 100) : 100;
    const streak = q.streak ? `🔥 ${q.streak} day${q.streak > 1 ? "s" : ""} in a row${q.cleared_today ? "" : " · today still open"}` : "no streak yet: bring the list to zero";
    const line = open === 0 ? (max ? `all ${max} done, nothing waits on you` : "nothing waited on you today") : `${done} of ${max} done, ${open} open`;
    const days = last(q.days || [], 14).filter(x => x.last != null), per = days.map(x => x.last);
    const trend = days.length > 1 ? ` <span title="items open at the end of each day, 14 days">${spark(per, "c-purple", days.map(x => x.day))}</span>` : "";
    return `<div class="card"><h3>Today's quest${open === 0 ? tag("clear", "green") : tag(open + " open", "amber")}</h3>
      <div class="big">${pct} %<small>${esc(line)}</small></div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="dim">${esc(streak)}${trend}</div>${fleetLine(d)}${doneList(d, q.done)}</div>`;
  }
  const spend14 = d => { const h = hist(d), days = last(h.days, 14); if (!days.length) return "";
    const per = days.map((_, i) => Object.values(h.bots).reduce((s, b) => s + (last(b.cost || [], 14)[i] || 0), 0));
    return `<div class="dim" style="font-size:11px;margin-top:6px">spent per day, 14 days ${spark(per, "c-blue", days, money)} ${money(sum(per))}</div>`; };
  function mana(d) {
    const bots = activeBots(d);
    const budget = bots.reduce((s, b) => s + budgetOf(d, b), 0), spent = bots.reduce((s, b) => s + (b.cost_today || 0), 0);
    const runs = bots.reduce((s, b) => s + (b.rounds_today || 0), 0);
    const left = Math.max(0, budget - spent), pct = budget ? Math.round(left / budget * 100) : 0;
    const rows = bots.map(b => { const m = manaOf(d, b); return `<div title="${esc(b.name)}: ${money(m.spent)} of ${money(m.budget)}"><a href="${blink(b.name)}">${esc(nameOf(b))}</a> ${money(m.left)}${manaBar(m, true)}</div>`; }).join("");
    return `<div class="card"><h3>Mana <small>${runs} runs today</small></h3>
      <div class="big">${money(left)}<small>left of ${money(budget)} · ${money(spent)} spent</small></div>
      ${manaBar({pct})}
      <div class="manas">${rows}</div>${spend14(d)}</div>`;
  }

  // ---- the cards ----
  const fig = (d, b) => botFigure(b, withRounds(b) ? manaBar(manaOf(d, b), true) : "", shoulder(d, b));
  function projectCard(d, p, items) {
    const bots = projectBots(d, p), mine = items.filter(i => i.project === p.name), merges = mine.filter(i => i.kind === "merge").length;
    const chips = [...projectChips(d, p, items), starBtn(p.name)];
    if (!bots.length && merges) chips.unshift(tag(`${merges} merge`, "green"));
    const body = bots.length ? `${pipelineBar(d, p, items)}<div class="bots">${bots.map(b => fig(d, b)).join("")}</div>` : slimLine(d, p);
    return `<div class="card proj${isFav(p.name) ? " fav" : ""}"><h3><a href="${plink(p.name)}">${esc(p.name)}</a><span class="chips">${chips.join("")}</span></h3>
      ${levelLine(d, p)}${body}</div>`;
  }
  function levelCards(d, p) {
    const x = lvOf(d, "projects", p.name), h = hp(d, p.name); if (!x || !h) return "";
    const days = hist(d).days, bots = d.bots.filter(b => b.project === p.name && hb(d, b.name)), X = XP();
    const rounds = days.map((_, i) => bots.reduce((n, b) => n + ((hb(d, b.name).rounds || [])[i] || 0), 0));
    const cost = days.map((_, i) => bots.reduce((n, b) => n + ((hb(d, b.name).cost || [])[i] || 0), 0));
    return `<div class="top" style="margin-top:12px"><div class="card"><h3>Level ${lvTag(x)}</h3>
        <div class="big">${x.xp} XP<small>${levelFloor(x.lv + 1) - x.xp} to level ${x.lv + 1}</small></div>${xpBar(x)}
        <table class="hist"><tr><td class="n">merged</td><td>${x.merged} pull requests × ${X.merged} = ${x.merged * X.merged}</td></tr>
          <tr><td class="n">closed</td><td>${x.closed} issues × ${X.closed} = ${x.closed * X.closed}</td></tr>
          <tr><td class="n">rounds</td><td>${x.done} done × ${X.done} = ${x.done * X.done}${x.bad ? ` <span class="dim">· ${x.bad} bad</span>` : ""}</td></tr></table>
        ${badges(x.badges)}${h.stale ? `<div class="dim hint" style="margin-top:6px">history is from an earlier run today: ${esc(h.error || "")}</div>` : ""}</div>
      <div class="card"><h3>History <small>${days.length} days</small></h3>
        <table class="hist"><tr><td class="n">merged</td><td>${spark(h.merged, "big", days)} <span class="dim">${sum(h.merged)}</span></td></tr>
          <tr><td class="n">closed</td><td>${spark(h.closed, "big c-purple", days)} <span class="dim">${sum(h.closed)}</span></td></tr>
          ${bots.length ? `<tr><td class="n">rounds</td><td>${spark(rounds, "big c-blue", days)} <span class="dim">${sum(rounds)}, ${money(sum(cost))}</span></td></tr>` : ""}</table>
        <div class="dim" style="font-size:11px">by: ${Object.entries(h.merged_by || {}).sort((a, b) => sum(b[1]) - sum(a[1])).map(([a, v]) => `${esc(who(a))} ${sum(v)}`).join(" · ") || "–"}</div></div></div>`;
  }
  const manaRow = d => b => `<tr><td class="n">mana</td><td>${(m => `${money(m.left)} left of ${money(m.budget)}${manaBar(m, true)}`)(manaOf(d, b))}</td></tr>`;
  function historyCard(d, b, x) {
    const h = hb(d, b.name) || {}, days = hist(d).days, X = XP();
    return `<div class="top" style="margin-top:12px"><div class="card"><h3>Level ${lvTag(x)}</h3>
        <div class="big">${x.xp} XP<small>${levelFloor(x.lv + 1) - x.xp} to level ${x.lv + 1}</small></div>${xpBar(x)}
        <table class="hist"><tr><td class="n">merged</td><td>${x.merged} pull requests × ${X.merged} = ${x.merged * X.merged}</td></tr>
          <tr><td class="n">rounds</td><td>${x.done} done × ${X.done} = ${x.done * X.done} <span class="dim">· ${h.rounds_total || 0} rounds since ${esc(h.since || "?")}, ${money(h.cost_total || 0)}${h.bad_total ? `, ${h.bad_total} bad` : ""}</span></td></tr></table>
        ${badges(x.badges)}</div>
      <div class="card"><h3>History <small>${days.length} days</small></h3>
        <table class="hist"><tr><td class="n">rounds</td><td>${spark(h.rounds || [], "big c-blue", days)} <span class="dim">${sum(h.rounds)}</span></td></tr>
          <tr><td class="n">done</td><td>${spark(h.done || [], "big", days)} <span class="dim">${sum(h.done)}${sum(h.bad) ? `, ${sum(h.bad)} bad` : ""}</span></td></tr>
          <tr><td class="n">cost</td><td>${spark(h.cost || [], "big c-purple", days, money)} <span class="dim">${money(sum(h.cost))}</span></td></tr></table></div></div>`;
  }

  F.set("fantasy", {
    title: "fantasy",
    pinned: d => d.projects.filter(p => isFav(p.name)).map(p => p.name),
    css: `
      .bar.mana i { background:var(--blue); }
      .bar.mana.low { outline:2px solid var(--red); outline-offset:1px; animation:lowmana 1.2s ease-in-out infinite; }
      @keyframes lowmana { 0%,100% { box-shadow:0 0 0 0 rgba(224,108,117,0); } 50% { box-shadow:0 0 0 5px rgba(224,108,117,.45); } }
      .bar.xp i { background:var(--purple); }
      .lv { display:inline-block; font-size:11px; font-weight:700; line-height:17px; padding:0 6px; border-radius:9px; background:var(--purple); color:#111; white-space:nowrap; }
      .lv.s { font-size:10px; line-height:14px; padding:0 4px; font-weight:600; margin-left:4px; vertical-align:1px; }
      .lvl { display:flex; align-items:center; gap:6px; font-size:11px; color:var(--dim); margin:-2px 0 6px; }
      .lvl .bar { flex:1; max-width:110px; margin:0; } .lvl .spark { margin-left:auto; }
      .badges { display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; } .badges .tag { cursor:help; color:#111; } .badges .tag.off { opacity:.45; background:var(--line); color:var(--fg); }
      .badges .tag b { font-weight:600; } .badges .tag small { font-size:10px; opacity:.75; margin-left:3px; }
      .rk0 { background:#a8795a; } .rk1 { background:#d39a6a; } .rk2 { background:#c9ced6; } .rk3 { background:#f2c94c; } .rk4 { background:#9fdcf0; } .rk5 { background:#d8b4fe; }
      .manas { display:flex; flex-wrap:wrap; gap:6px 14px; margin-top:6px; font-size:11px; color:var(--dim); }
      .manas div { min-width:70px; flex:1; } .manas .bar { margin:2px 0 1px; }
      input.budget { width:56px; font:inherit; font-size:13px; background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:4px; padding:0 4px; }
      .card.fav { border-color:var(--star); }
      .star { background:none; border:none; cursor:pointer; font-size:16px; line-height:1; color:var(--line); padding:0 2px; }
      .star.on { color:var(--star); } .star:hover { color:var(--star); }
      .fig .lvb { position:absolute; right:-7px; bottom:-3px; font-size:10px; line-height:14px; padding:0 4px; border-radius:7px; background:var(--purple); color:#111; font-weight:700; box-shadow:0 0 0 2px var(--card); cursor:help; }
      .fig.large .lvb { font-size:14px; line-height:20px; padding:0 7px; border-radius:10px; right:6px; bottom:8px; }
      .confetti { position:fixed; top:-12px; width:8px; height:12px; border-radius:2px; animation:fall 2.6s ease-in forwards; pointer-events:none; z-index:9; }
      @keyframes fall { to { transform:translateY(105vh) rotate(600deg); opacity:.3; } }
      .donelist { margin:8px 0 0; padding:6px 0 0; border-top:1px solid var(--line); list-style:none; font-size:12px; color:var(--dim); }
      .donelist li { margin:2px 0; padding-left:16px; position:relative; } .donelist li::before { content:"✓"; position:absolute; left:2px; color:var(--green); font-weight:700; }
      .donelist a { color:var(--dim); } .donelist .tag { opacity:.7; } .donelist .p { font-size:11px; margin-left:4px; }
    `,
    overview(d) {
      const items = actions(d);
      const fav = d.projects.filter(p => isFav(p.name)), rest = d.projects.filter(p => !isFav(p.name));
      const g = d.machine.gpu || {};
      return `<div class="top">${quest(d, items.length)}${mana(d)}</div>
        ${fav.length ? `<div class="grid" style="margin-bottom:12px">${fav.map(p => projectCard(d, p, items)).join("")}</div>` : ""}
        ${todo(items)}
        <h2>${fav.length ? "Other projects" : "Projects"}<small>${fav.length ? "" : "★ pins a project under the quest"}</small></h2>
        <div class="grid">${rest.map(p => projectCard(d, p, items)).join("")}</div>
        <div class="foot">
          <span>guards: <a href="#machine">${guardsLine(d)}</a></span>
          <span><a href="#machine">${esc(d.machine.name)}</a>${g.lock ? " " + tag("GPU lock", "amber") : ""}</span>
        </div>`;
    },
    project(d, p) {
      const items = actions(d), mine = items.filter(i => i.project === p.name), bots = d.bots.filter(b => b.project === p.name);
      return `${p.error ? `<div class="err">${esc(p.error)}</div>` : ""}
        <div class="card proj"><h3>Pipeline${starBtn(p.name)}</h3>${pipelineBar(d, p, items)}
          ${mine.length ? `<ul class="todo">${mine.map(i => `<li>${tag(i.tag, i.cls)} ${i.html}</li>`).join("")}</ul>` : `<div class="dim">nothing waits on you here</div>`}</div>
        ${levelCards(d, p)}
        ${prsTable(p)}${issuesTable(p)}
        <h2>Bots</h2>
        ${bots.length ? `<div class="grid">${bots.map(b => botCard(b, manaRow(d), b => shoulder(d, b))).join("")}</div>` : `<div class="dim">no bots of its own</div>`}`;
    },
    bot(d, b) {
      const x = lvOf(d, "bots", b.name), m = manaOf(d, b);
      const manaCard = withRounds(b) ? `<div class="card"><h3>Mana <small>today</small></h3>
          <div class="big">${money(m.left)}<small>left of $<input class="budget" data-bot="${esc(b.name)}" type="number" min="0" step="0.5" value="${m.budget}" title="this bot's daily budget, kept in this browser"> · ${money(m.spent)} spent</small></div>
          ${manaBar(m)}<div class="dim">the bar empties as ${esc(nameOf(b))} spends; the budget is this browser's</div></div>` : "";
      return `${hero(d, b, x ? ` · level ${x.lv}` : "", shoulder(d, b))}
        <div class="top" style="margin-top:12px"><div class="card"><h3>Rounds</h3>${roundsTable(b)}</div>${manaCard}</div>${x ? historyCard(d, b, x) : ""}`;
    },
    machine(d) {
      return `<h2>Guards</h2><div class="grid">${d.guards.map(guard).join("")}</div>
        <h2>Machine</h2><div class="grid">${machine(d.machine)}</div>
        <h2>Guard bots</h2><div class="grid">${d.bots.filter(b => b.kind === "guard").map(b => botCard(b, manaRow(d), b => shoulder(d, b))).join("")}</div>`;
    },
    after(root) {
      for (const s of root.querySelectorAll("button.star")) s.onclick = () => { toggleFav(s.dataset.fav); F.render(); };
      for (const inp of root.querySelectorAll("input.budget")) inp.onchange = () => { const v = Number(inp.value); store.set(`fleet.budget.${inp.dataset.bot}`, Number.isFinite(v) && v >= 0 ? v : null); F.render(); };
    },
  });
})();
