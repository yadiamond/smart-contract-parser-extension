import * as vscode from 'vscode';
import { execFile } from 'child_process';
import * as path from 'path';

interface GeneratedMethod {
	signature: string;
	kind: 'view' | 'tx';
	line: number;
}

interface ContractEntry {
	name: string;
	address: string;
	chain: string;
	file: string;
	methods: GeneratedMethod[];
}

interface GeneratorOutput {
	ok: boolean;
	file?: string;
	name?: string;
	address?: string;
	chain?: string;
	functions?: unknown;
	error?: string;
}

const NETWORKS: ReadonlyArray<{ id: number; name: string }> = [
	{ id: 1, name: 'Ethereum' },
	{ id: 11155111, name: 'Sepolia' },
	{ id: 56, name: 'BNB Smart Chain' },
	{ id: 137, name: 'Polygon' },
	{ id: 42161, name: 'Arbitrum One' },
	{ id: 8453, name: 'Base' },
	{ id: 10, name: 'Optimism' },
	{ id: 43114, name: 'Avalanche' },
];

interface NetworkPickItem extends vscode.QuickPickItem {
	network?: { id: number; name: string };
}

class ContractNode {
	constructor(readonly contract: ContractEntry) {}
}

class MethodNode {
	constructor(readonly contract: ContractEntry, readonly method: GeneratedMethod) {}
}

type TreeNode = ContractNode | MethodNode;

class ContractStore {
	private static readonly STORAGE_KEY = 'smartContractParser.contracts.v2';

	constructor(private readonly context: vscode.ExtensionContext) {}

	all(): ContractEntry[] {
		return this.context.workspaceState.get<ContractEntry[]>(ContractStore.STORAGE_KEY, []);
	}

	async add(entry: ContractEntry): Promise<void> {
		const rest = this.all().filter((item) => contractKey(item) !== contractKey(entry));
		await this.context.workspaceState.update(ContractStore.STORAGE_KEY, [entry, ...rest]);
	}

	async remove(address: string, chain: string): Promise<void> {
		const rest = this.all().filter((item) => contractKey(item) !== `${address.toLowerCase()}:${chain}`);
		await this.context.workspaceState.update(ContractStore.STORAGE_KEY, rest);
	}
}

class ContractsProvider implements vscode.TreeDataProvider<TreeNode> {
	private readonly changeEmitter = new vscode.EventEmitter<TreeNode | undefined>();
	readonly onDidChangeTreeData: vscode.Event<TreeNode | undefined> = this.changeEmitter.event;

	constructor(private readonly store: ContractStore) {}

	refresh(): void {
		this.changeEmitter.fire(undefined);
	}

	getTreeItem(element: TreeNode): vscode.TreeItem {
		if (element instanceof ContractNode) {
			const item = new vscode.TreeItem(element.contract.name, vscode.TreeItemCollapsibleState.Expanded);
			item.description = `${shortAddress(element.contract.address)} · ${element.contract.chain}`;
			item.iconPath = new vscode.ThemeIcon('file-code');
			item.contextValue = 'contract';
			item.tooltip = new vscode.MarkdownString(
				`**${element.contract.name}**\n\nСеть: ${element.contract.chain}\n\n\`${element.contract.address}\``,
			);
			return item;
		}

		const item = new vscode.TreeItem(element.method.signature, vscode.TreeItemCollapsibleState.None);
		item.iconPath = new vscode.ThemeIcon(element.method.kind === 'view' ? 'eye' : 'zap');
		item.command = {
			command: 'smart-contract-parser.openMethod',
			title: 'Открыть функцию',
			arguments: [element],
		};
		return item;
	}

	getChildren(element?: TreeNode): TreeNode[] {
		if (!element) {
			return this.store.all().map((contract) => new ContractNode(contract));
		}
		if (element instanceof ContractNode) {
			return element.contract.methods.map((method) => new MethodNode(element.contract, method));
		}
		return [];
	}
}

function shortAddress(address: string): string {
	return `${address.slice(0, 8)}…${address.slice(-6)}`;
}

function contractKey(entry: ContractEntry): string {
	return `${entry.address.toLowerCase()}:${entry.chain}`;
}

function parseGeneratorOutput(rawOutput: string): GeneratorOutput | null {
	const outputLines = rawOutput.trim().split('\n');
	for (const line of outputLines.reverse()) {
		try {
			const parsed: unknown = JSON.parse(line);
			if (parsed !== null && typeof parsed === 'object' && 'ok' in parsed) {
				return parsed as GeneratorOutput;
			}
		} catch {
			continue;
		}
	}
	return null;
}

function toContractEntry(output: GeneratorOutput): ContractEntry | null {
	if (
		!output.ok ||
		typeof output.file !== 'string' ||
		typeof output.name !== 'string' ||
		typeof output.address !== 'string' ||
		!Array.isArray(output.functions)
	) {
		return null;
	}

	const methods: GeneratedMethod[] = [];
	for (const raw of output.functions) {
		if (raw === null || typeof raw !== 'object') {
			continue;
		}
		const record = raw as Record<string, unknown>;
		if (
			typeof record.signature === 'string' &&
			(record.kind === 'view' || record.kind === 'tx') &&
			typeof record.line === 'number'
		) {
			methods.push({ signature: record.signature, kind: record.kind, line: record.line });
		}
	}
	return {
		name: output.name,
		address: output.address,
		chain: typeof output.chain === 'string' ? output.chain : 'Ethereum',
		file: output.file,
		methods,
	};
}

function runGeneratorScript(pythonPath: string, args: string[]): Promise<string> {
	return new Promise((resolve, reject) => {
		execFile(pythonPath, args, { maxBuffer: 16 * 1024 * 1024 }, (error, stdout, stderr) => {
			if (stdout.trim().length > 0) {
				resolve(stdout);
				return;
			}
			reject(error ?? new Error(stderr || 'Python-скрипт вернул пустой ответ'));
		});
	});
}

async function askNetwork(): Promise<{ id: number; name: string } | undefined> {
	const items: NetworkPickItem[] = NETWORKS.map((network) => ({
		label: network.name,
		description: `chainid ${network.id}`,
		network,
	}));
	items.push({ label: 'Другая сеть…', description: 'ввести chainid вручную' });

	const picked = await vscode.window.showQuickPick(items, {
		placeHolder: 'Сеть смарт-контракта',
		ignoreFocusOut: true,
	});
	if (!picked) {
		return undefined;
	}
	if (picked.network) {
		return picked.network;
	}

	const entered = await vscode.window.showInputBox({
		prompt: 'Введите chainid сети из списка поддерживаемых Etherscan V2 (docs.etherscan.io/supported-chains)',
		placeHolder: 'например: 8453',
		validateInput: (value) => (/^\d+$/.test(value.trim()) ? null : 'chainid — целое число'),
	});
	const chainId = Number(entered?.trim());
	if (!Number.isInteger(chainId)) {
		return undefined;
	}
	return { id: chainId, name: `chain-${chainId}` };
}

async function askAddress(): Promise<string | undefined> {
	const entered = await vscode.window.showInputBox({
		prompt: 'Адрес смарт-контракта в выбранной сети',
		placeHolder: '0x…',
		validateInput: (value) =>
			/^0[xX][0-9a-fA-F]{40}$/.test(value.trim())
				? null
				: 'Адрес должен начинаться с 0x и содержать 40 шестнадцатеричных символов',
	});
	return entered?.trim();
}

async function askApiKey(): Promise<string | undefined> {
	const config = vscode.workspace.getConfiguration('smart-contract-parser');
	const saved = config.get<string>('etherscanApiKey', '').trim();
	if (saved.length > 0) {
		return saved;
	}

	const entered = await vscode.window.showInputBox({
		prompt: 'Введите бесплатный API-ключ Etherscan (etherscan.io → Settings → API keys). Ключ сохранится в настройках.',
		placeHolder: 'API-ключ Etherscan',
		password: true,
		ignoreFocusOut: true,
	});
	const trimmed = entered?.trim();
	if (!trimmed) {
		return undefined;
	}
	await config.update('etherscanApiKey', trimmed, vscode.ConfigurationTarget.Global);
	return trimmed;
}

async function askOutDir(): Promise<string | undefined> {
	const folders = vscode.workspace.workspaceFolders;
	if (folders && folders.length > 0) {
		return folders[0].uri.fsPath;
	}
	const picked = await vscode.window.showOpenDialog({
		canSelectFolders: true,
		canSelectFiles: false,
		canSelectMany: false,
		openLabel: 'Сохранить сюда',
		title: 'Куда сохранить сгенерированный файл',
	});
	return picked?.[0]?.fsPath;
}

async function openFileAtLine(filePath: string, lineNumber: number): Promise<void> {
	const document = await vscode.workspace.openTextDocument(filePath);
	const position = new vscode.Position(Math.max(lineNumber - 1, 0), 0);
	await vscode.window.showTextDocument(document, {
		selection: new vscode.Range(position, position),
		preview: false,
	});
}

export function activate(context: vscode.ExtensionContext) {
	const store = new ContractStore(context);
	const provider = new ContractsProvider(store);

	context.subscriptions.push(
		vscode.window.createTreeView('smartContractExplorer', { treeDataProvider: provider }),
		vscode.commands.registerCommand('smart-contract-parser.refreshTree', () => provider.refresh()),
		vscode.commands.registerCommand('smart-contract-parser.removeContract', async (node: ContractNode) => {
			await store.remove(node.contract.address, node.contract.chain);
			provider.refresh();
		}),
		vscode.commands.registerCommand('smart-contract-parser.openMethod', async (node: MethodNode) => {
			await openFileAtLine(node.contract.file, node.method.line);
		}),
		vscode.commands.registerCommand('smart-contract-parser.generateMethods', async () => {
			const network = await askNetwork();
			if (!network) {
				return;
			}
			const address = await askAddress();
			if (!address) {
				return;
			}
			const apiKey = await askApiKey();
			if (!apiKey) {
				return;
			}
			const outDir = await askOutDir();
			if (!outDir) {
				vscode.window.showErrorMessage(
					'Smart Contract Parser: откройте папку проекта, чтобы сохранить сгенерированный файл',
				);
				return;
			}

			const config = vscode.workspace.getConfiguration('smart-contract-parser');
			const pythonPath = config.get<string>('pythonPath', 'python3');
			const scriptPath = path.join(context.extensionPath, 'python', 'generate_methods.py');

			try {
				const rawOutput = await vscode.window.withProgress(
					{ location: vscode.ProgressLocation.Notification, title: 'Smart Contract Parser: получаю ABI и генерирую методы…' },
					() => runGeneratorScript(pythonPath, [
						scriptPath,
						'--address', address,
						'--chainid', String(network.id),
						'--apikey', apiKey,
						'--out', outDir,
					]),
				);

				const output = parseGeneratorOutput(rawOutput);
				const entry = toContractEntry(output ?? { ok: false });
				if (!entry) {
					const reason = output?.error ?? 'скрипт вернул неожиданный ответ';
					vscode.window.showErrorMessage(`Smart Contract Parser: ${reason}`);
					return;
				}

				await store.add(entry);
				provider.refresh();
				await openFileAtLine(entry.file, 1);
				vscode.window.showInformationMessage(
					`Smart Contract Parser: ${entry.name} — сгенерировано методов: ${entry.methods.length}`,
				);
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				vscode.window.showErrorMessage(
					`Smart Contract Parser: ${message}. Проверьте, что Python установлен, ` +
					'или укажите путь к нему в настройке smart-contract-parser.pythonPath',
				);
			}
		}),
	);
}
