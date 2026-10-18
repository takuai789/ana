"""Run: python3 -m unittest discover -s tests -v (requires Playwright + Chromium)."""
from datetime import datetime, timezone
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
from playwright.sync_api import sync_playwright


class BrowserRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(Path(__file__).resolve().parents[1]))
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/'
        cls.playwright = sync_playwright().start()
        executable = os.environ.get('CHROMIUM_PATH') or shutil.which('chromium') or shutil.which('google-chrome')
        cls.browser = cls.playwright.chromium.launch(**({'executable_path': executable} if executable else {}))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.context = self.browser.new_context(service_workers='block', timezone_id='Asia/Tokyo')
        self.page = self.context.new_page()
        self.page.clock.install(time=datetime(2026, 10, 18, tzinfo=timezone.utc))
        self.page.goto(self.url)
        self.page.wait_for_function('state.settings !== null')

    def tearDown(self):
        self.context.close()

    def test_normal_record_and_export(self):
        for view in ['log', 'plan', 'tasks', 'settings', 'home']:
            self.page.locator(f'.tab[data-v="{view}"]').click()
            self.assertTrue(self.page.locator(f'#view-{view}').evaluate('(e)=>e.classList.contains("active")'))
        self.page.locator('#homeAdd').click()
        self.page.locator('#sd').fill('2027-01-31')
        self.page.locator('#sc').fill('25')
        self.page.locator('#sheetSave').click()
        self.page.wait_for_function('state.allSnaps.length === 1')
        self.page.reload()
        self.page.wait_for_function('state.allSnaps.length === 1')
        self.assertEqual(self.page.evaluate('state.allSnaps[0].cumulative'), 250000)
        self.page.locator('.tab[data-v="settings"]').click()
        with self.page.expect_download() as info:
            self.page.locator('#btnExport').click()
        data = json.loads(Path(info.value.path()).read_text())
        self.assertEqual(data['schema'], 'ana300.v3')
        self.assertEqual(data['snapshots'][0]['cumulative'], 250000)

    def test_reject_xss_import(self):
        self.assertEqual(self.page.evaluate('''async()=>{const id='x"><img src="/missing" onerror="window.pwned=1">'; const ok=await importData(new File([JSON.stringify({schema:SCHEMA,tasks:[{id,sheetId:state.settings.id,text:'x',done:false}]})],'x.json'));switchView('tasks');return [ok,await getAll('tasks'),!!window.pwned];}'''), [False, [], False])

    def test_atomic_invalid_import(self):
        self.assertEqual(self.page.evaluate('''async()=>{const ok=await importData(new File([JSON.stringify({schema:SCHEMA,fixed:[{id:'valid',sheetId:state.settings.id,name:'valid',monthly:10000},{name:'invalid'}]})],'x.json'));return [ok,(await getAll('fixed')).length];}'''), [False, 0])

    def test_existing_identifier_is_escaped(self):
        self.assertEqual(self.page.evaluate('''async()=>{await put('tasks',{id:'x"><img src="/missing" onerror="window.pwned=1">',sheetId:state.settings.id,text:'safe',done:false});await loadAll();switchView('tasks');return [document.querySelectorAll('#tasksScroll img').length,!!window.pwned];}'''), [0,False])

    def test_import_write_failure_rolls_back(self):
        self.assertEqual(self.page.evaluate('''async()=>{const original=IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(value,...args){if(value.id==='boom')throw new Error('simulated write failure');return original.call(this,value,...args);};try{const rows=['first','boom'].map(id=>({id,sheetId:state.settings.id,name:id,monthly:10000}));const ok=await importData(new File([JSON.stringify({schema:SCHEMA,fixed:rows})],'x.json'));return [ok,(await getAll('fixed')).length,(await getAll('sheets')).length];}finally{IDBObjectStore.prototype.put=original;}}'''), [False,0,3])

    def test_tasks_only_backup_notification(self):
        self.assertTrue(self.page.evaluate('''async()=>{await put('tasks',{id:'task',sheetId:state.settings.id,text:'task',done:false});await loadAll();return backupDue();}'''))

    def test_transaction_rollback(self):
        self.assertEqual(self.page.evaluate('''async()=>{try{await writeDB(['tasks'],tx=>{tx.objectStore('tasks').put({id:'rollback'});tx.abort();});return false;}catch{return (await getAll('tasks')).length===0;}}'''), True)

    def test_restore_new_browser_and_repeat(self):
        backup = self.page.evaluate('''async()=>{await put('tasks',{id:'restoredtask',sheetId:state.activeList[2].id,text:'restore',done:false});await loadAll();state.prefs.active_sheet=state.activeList[2].id;return backupJSON();}''')
        with self.browser.new_context(service_workers='block') as context:
            page = context.new_page(); page.clock.install(time=datetime(2026, 10, 18, tzinfo=timezone.utc)); page.goto(self.url); page.wait_for_function('state.settings !== null')
            for _ in range(2):
                result = page.evaluate('''async text=>{const ok=await importData(new File([text],'backup.json'));switchView('tasks');return [ok,state.sheets.length,state.activeList.length,state.tasks.length];}''', backup)
                self.assertEqual(result, [True, 3, 3, 1])

    def test_reject_overlap_without_changes(self):
        self.assertEqual(self.page.evaluate('''async()=>{await put('tasks',{id:'local',sheetId:state.settings.id,text:'keep',done:false});await loadAll();const sh={...state.settings,id:'different'};const ok=await importData(new File([JSON.stringify({schema:SCHEMA,sheets:[sh]})],'x.json'));return [ok,(await getAll('sheets')).length,(await getAll('tasks')).length];}'''), [False,3,1])

    def test_reject_orphan_and_invalid_date(self):
        self.assertEqual(self.page.evaluate('''async()=>{const rows=[{id:'orphan',sheetId:'unknown',date:'2027-01-31',cumulative:1},{id:'badDate',sheetId:state.settings.id,date:'2027-02-30',cumulative:1}];const results=[];for(const row of rows) results.push(await importData(new File([JSON.stringify({schema:SCHEMA,snapshots:[row]})],'x.json')));return [...results,(await getAll('snapshots')).length];}'''), [False,False,0])

    def test_legacy_v2(self):
        self.assertEqual(self.page.evaluate('''async()=>{const ok=await importData(new File([JSON.stringify({schema:'ana300.v2',snapshots:[{id:'legacy',date:'2027-01-31',cumulative:250000}]})],'v2.json'));return [ok,state.snaps[0].sheetId===state.settings.id,state.snaps[0].cumulative];}'''), [True,True,250000])

    def test_two_tab_setting_update(self):
        other = self.context.new_page(); other.clock.install(time=datetime(2026, 10, 18, tzinfo=timezone.utc)); other.goto(self.url); other.wait_for_function('state.settings !== null')
        self.page.evaluate('saveSheet({target_amount:4000000})')
        other.evaluate('saveSheet({assumed_monthly:300000})')
        self.assertEqual(other.evaluate('[state.settings.target_amount,state.settings.assumed_monthly]'), [4000000,300000])

    def test_start_month_and_backup_notification(self):
        self.assertEqual(self.page.evaluate('''async()=>{await put('oneoffs',{id:'one',sheetId:state.settings.id,name:'December',month:'2026-12',amount:500000});await loadAll();return [forecast().oneoffRemaining,backupDue()];}'''), [500000,True])

    def test_midmonth_goal(self):
        self.assertEqual(self.page.evaluate('''()=>{state.settings={...state.settings,period_start:'2026-12-16',period_end:'2027-12-15',goal_deadline:'2027-01-15',assumed_monthly:0,target_amount:100000};state.fixed=[{monthly:100000}];const f=forecast();return [f.landing,f.fixedRemaining,f.goalName];}'''), [0,0,'1月15日'])

    def test_negative_amount_and_duplicate_month_edit(self):
        self.assertEqual(self.page.evaluate('''async()=>{switchView('plan');openFixedSheet(null);$('#fn').value='negative';$('#fm').value='-10';await sheetSave();const negative=state.fixed.length;for(const [id,date] of [['a','2027-01-31'],['b','2027-02-28']])await put('snapshots',{id,date,cumulative:100000,sheetId:state.settings.id});await loadAll();openSnapSheet('a');$('#sd').value='2027-02-20';const result=await sheetSave();return [negative,result,(await getAll('snapshots')).find(x=>x.id==='a').date];}'''), [0,False,'2027-01-31'])

    def test_numeric_limits_and_exponent(self):
        self.assertEqual(self.page.evaluate('''async()=>{const before=state.settings.target_amount;await saveSheet({target_amount:-1});await saveSheet({target_amount:Infinity});return [state.settings.target_amount===before,manToYen('1e2'),validMoney(manToYen('1e100'))];}'''), [True,1000000,False])

    def test_invalid_period_not_saved(self):
        self.assertTrue(self.page.evaluate('''async()=>{const before=state.settings.period_start;const ok=await saveSheet({period_start:'2028-01-01'});return ok===false&&(await getAll('sheets')).find(s=>s.id===state.settings.id).period_start===before;}'''))

    def test_graph_bounded(self):
        self.assertLess(self.page.evaluate('''()=>{state.settings.target_amount=100000000000;return (chartSVG(forecast()).match(/<text/g)||[]).length;}'''), 30)

    def test_cache_namespace(self):
        self.assertEqual(self.page.evaluate('''async()=>{await caches.open('other-app');await caches.open('ana300-old');const listeners={};const source=await(await fetch('sw.js')).text();new Function('self','caches',source)({addEventListener:(n,f)=>listeners[n]=f,clients:{claim:async()=>{}}},caches);let done;listeners.activate({waitUntil:p=>done=p});await done;const keys=await caches.keys();return [keys.includes('other-app'),keys.includes('ana300-old')];}'''), [True,False])

    def test_service_worker_offline(self):
        with self.browser.new_context(timezone_id='Asia/Tokyo') as context:
            page=context.new_page();page.clock.install(time=datetime(2026, 10, 18, tzinfo=timezone.utc));page.goto(self.url)
            page.wait_for_function('navigator.serviceWorker.controller !== null')
            page.wait_for_function('typeof state !== "undefined" && state.settings !== null')
            context.set_offline(True);page.reload(wait_until='domcontentloaded')
            page.wait_for_function('state.settings !== null')
            self.assertEqual(page.title(),'300万メーター')
