// Entry point: registers every screen, then boots the shell.
import { app } from './app.js';
import './screens/gateway.js';
import './screens/masters.js';
import './screens/voucher.js';
import './screens/reports.js';

app.start();
