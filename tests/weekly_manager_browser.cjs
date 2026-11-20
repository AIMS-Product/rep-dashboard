/* Browser acceptance for fixture releases staged by test_weekly_manager_release.py. */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');

const { chromium } = require(process.env.WEEKLY_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(process.argv[2]);
const origin = 'http://localhost';

async function csvRows(page) {
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download CSV' }).click();
    const download = await downloadPromise;
    const rows = (await fs.readFile(await download.path(), 'utf8')).trim().split(/\r?\n/)
        .map(line => line.split(',').map(cell => cell.replace(/^"|"$/g, '')));
    return { filename: download.suggestedFilename(), rows };
}

async function main() {
    const browser = await chromium.launch({
        executablePath: process.env.WEEKLY_CHROMIUM,
        headless: true,
    });
    try {
        const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1440, height: 900 } });
        const responses = { '.html': 'text/html', '.json': 'application/json' };
        await context.route('**/*', async route => {
            const url = new URL(route.request().url());
            // Block external resources so the test uses the local fixture exclusively.
            if (url.origin !== origin) return route.abort();
            const file = path.resolve(root, '.' + url.pathname);
            if (!file.startsWith(root + path.sep) || !url.pathname.includes('/public/')) {
                return route.fulfill({ status: 404 });
            }
            try {
                const body = await fs.readFile(file);
                await route.fulfill({ body, contentType: responses[path.extname(file)], status: 200 });
            } catch (error) {
                if (error.code !== 'ENOENT') throw error;
                await route.fulfill({ status: 404 });
            }
        });
        const page = await context.newPage();
        page.setDefaultTimeout(5000);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(origin + '/november/public/manager.html');
        await page.waitForURL('**/releases/**/manager.html');
        const period = page.getByLabel('Reporting period');
        await period.waitFor();
        assert.equal(await period.inputValue(), 'month');
        let csv = await csvRows(page);
        assert.equal(csv.filename, 'sales-manager-mtd-2026-11.csv');
        assert.equal(csv.rows.find(row => row[0] === 'Team Totals')[2], '2');
        assert.equal(csv.rows[0][10], 'QA Score (/5)');

        await period.selectOption('2026-10-26');
        assert.match(await page.locator('#weekLabel').innerText(), /Oct 26, 2026.*Nov 01, 2026/);
        assert.match(await page.locator('#operatingSnapshot').innerText(), /Current task and pipeline snapshot/);
        const joePre = page.getByRole('button', { name: 'View pre-call adherence for Joe Dysert' });
        const joePost = page.getByRole('button', { name: 'View post-call adherence for Joe Dysert' });
        assert.match(await joePre.innerText(), /100%/);
        assert.match(await joePost.innerText(), /100%/);
        await joePost.focus();
        await page.keyboard.press('Enter');
        assert.equal(await joePost.getAttribute('aria-expanded'), 'true');
        const joeDetail = page.locator('#' + await joePost.getAttribute('aria-controls'));
        assert.match(await joeDetail.innerText(), /1 of 1 eligible/);
        const scottPost = page.getByRole('button', { name: 'View post-call adherence for Scott Seymour' });
        await scottPost.click();
        const scottDetail = page.locator('#' + await scottPost.getAttribute('aria-controls'));
        assert.match(await scottDetail.innerText(), /Transferred.*Reassigned/s);
        assert.match(await scottDetail.innerText(), /Exempt \(1\).*Lost/s);

        await page.getByRole('button', { name: 'View Calls Occurred records for Scott Seymour' }).click();
        const drawer = page.getByRole('dialog');
        assert.equal(await drawer.locator('.metric-drawer-item').count(), 2);
        assert.match(await drawer.innerText(), /Oct 26, 2026.*Nov 01, 2026/is);
        await drawer.getByRole('button', { name: 'Close details' }).click();
        csv = await csvRows(page);
        assert.equal(csv.filename, 'sales-manager-week-2026-10-26.csv');
        assert.deepEqual(csv.rows.find(row => row[0] === 'Team Totals').slice(2, 7), ['3', '3', '0', '1', '1250']);
        assert.deepEqual(csv.rows.find(row => row[0] === 'Joe Dysert').slice(13, 15), ['100%', '100%']);
        await page.reload();
        assert.equal(await period.inputValue(), '2026-10-26');
        await page.goto(origin + '/november/public/manager.html?period=2026-11-02');
        await page.waitForURL('**/releases/**/manager.html?period=2026-11-02');
        assert.equal(await period.inputValue(), '2026-11-02');
        csv = await csvRows(page);
        assert.equal(csv.filename, 'sales-manager-week-2026-11-02.csv');
        assert.equal(csv.rows.find(row => row[0] === 'Team Totals')[2], '1');

        await page.setViewportSize({ width: 390, height: 844 });
        await period.selectOption('2026-10-26');
        const controlWidth = await period.boundingBox();
        assert.ok(controlWidth.x >= 0 && controlWidth.x + controlWidth.width <= 390);
        const headerOverflow = await page.locator('.header').evaluate(header => header.scrollWidth > header.clientWidth);
        assert.equal(headerOverflow, false, 'the period control fits on mobile');

        await page.goto(origin + '/october/public/rep/index.html');
        const scottRepPost = page.locator('.adherence-board').getByRole('button', {
            name: /Open post-call adherence details for Scott Seymour/,
        });
        await scottRepPost.click();
        const repDrawer = page.locator('#adherenceDrawer');
        await repDrawer.getByText('Missed (1)', { exact: true }).first().click();
        await repDrawer.getByText('Exempt (1)', { exact: true }).first().click();
        assert.match(await repDrawer.innerText(), /Transferred.*Reassigned/s);
        assert.match(await repDrawer.innerText(), /Closed Lost opportunity/);
        assert.deepEqual(errors, []);
        console.log('Passed Chromium checks for period selection, Joe, ownership, drawers, CSVs, redirects and mobile layout.');
    } finally {
        await browser.close();
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
