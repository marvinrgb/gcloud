const express = require('express');
const puppeteer = require('puppeteer');
const { PuppeteerScreenRecorder } = require('puppeteer-screen-recorder');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { v4: uuidv4 } = require('uuid');

const app = express();
app.use(express.json({ limit: '50mb' }));

app.post('/convert', async (req, res) => {
    const svgContent = req.body.svg;
    const duration = req.body.duration || 3500; // default 3.5 seconds

    if (!svgContent) return res.status(400).send('No SVG provided');

    const jobId = uuidv4();
    const htmlPath = path.join(os.tmpdir(), `${jobId}.html`);
    const mp4Path = path.join(os.tmpdir(), `${jobId}.mp4`);

    // Wrap the SVG in a borderless HTML page
    const htmlContent = `<html><body style="margin:0; overflow:hidden; background:#e2e8f0;">${svgContent}</body></html>`;
    fs.writeFileSync(htmlPath, htmlContent);

    let browser;
    try {
        // Launch browser optimized for Docker/Cloud Run
        browser = await puppeteer.launch({
            headless: "new",
            args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
            executablePath: process.env.PUPPETEER_EXECUTABLE_PATH
        });

        const page = await browser.newPage();
        await page.setViewport({ width: 800, height: 600 }); // Match your SVG viewBox
        await page.goto(`file://${htmlPath}`);

        const recorder = new PuppeteerScreenRecorder(page, {
            fps: 30, // 30fps is stable for Cloud Run
        });

        await recorder.start(mp4Path);
        
        // Wait for the animation duration
        await new Promise(resolve => setTimeout(resolve, duration));
        
        await recorder.stop();
        await browser.close();

        // Send the file back to n8n as an attachment
        res.download(mp4Path, 'animation.mp4', (err) => {
            // Clean up files from /tmp to save memory
            if (fs.existsSync(htmlPath)) fs.unlinkSync(htmlPath);
            if (fs.existsSync(mp4Path)) fs.unlinkSync(mp4Path);
        });

    } catch (err) {
        console.error(err);
        if (browser) await browser.close();
        res.status(500).send('Error generating video');
    }
});

const port = process.env.PORT || 8080;
app.listen(port, () => console.log(`Listening on port ${port}`));