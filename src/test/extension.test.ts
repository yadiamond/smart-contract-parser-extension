import * as assert from 'assert';
import * as vscode from 'vscode';

suite('Extension Test Suite', () => {
	test('activates and registers Smart Contracts commands', async () => {
		const extension = vscode.extensions.all.find((item) => item.packageJSON.name === 'smart-contract-parser');
		assert.ok(extension, 'Smart Contract Parser extension was not found');

		await extension.activate();

		const commands = await vscode.commands.getCommands(true);
		for (const command of [
			'smart-contract-parser.generateMethods',
			'smart-contract-parser.removeContract',
			'smart-contract-parser.refreshTree',
			'smart-contract-parser.openMethod',
		]) {
			assert.ok(commands.includes(command), `Command is not registered: ${command}`);
		}
	});
});
