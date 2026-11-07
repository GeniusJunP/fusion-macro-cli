# fusion-macro-cli

[English](README.md)

Fusion のコンポジションから DaVinci Resolve のタイトルプリセット（`.setting`）を作成するための、AI エージェント向け CLI および Agent Skill です。

ユーザーが Fusion で見た目を制作し、エージェントがそれをプリセット化します。エージェントはコンポジションの抽出、公開する入力の宣言、`.setting` の組み立てと検査、Resolve 上でのレンダリングによるサムネイル生成、`.drfx` へのパッケージングまでを行います。最後にユーザーが Resolve 上でレンダリング結果とインスペクタの表示を目視で確認します。

`.setting` は次の 2 つの形式で作成できます。

- **Macro 形式**：宣言した入力のみを、指定した表示名でインスペクタに表示します。複数のツールを組み合わせることができます。
- **Tool 形式**：単一のツールをそのまま保存し、そのツール本来のインスペクタを表示します。

## 導入

エージェントに Skill と CLI の両方を導入します。

### 1. Skill

Claude Code の場合、本リポジトリをプラグインマーケットプレイスとして追加し、プラグインをインストールします。

```
/plugin marketplace add GeniusJunP/fusion-macro-cli
/plugin install fusion-macro-cli@fusion-macro-cli
```

Claude Code 2.1.275 以降では、`/plugin install fusion-macro-cli --marketplace GeniusJunP/fusion-macro-cli` の 1 コマンドでまとめて実行できます。シェルから実行する場合は、`claude plugin marketplace add GeniusJunP/fusion-macro-cli` および `claude plugin install fusion-macro-cli@fusion-macro-cli` を実行してください。

その他のエージェントの場合は、`skills/fusion-macro-cli/` を各エージェントのスキル読み込み先ディレクトリへコピーします。

```
git clone https://github.com/GeniusJunP/fusion-macro-cli
cp -R fusion-macro-cli/skills/fusion-macro-cli <Skill の読み込み先>/
```

### 2. CLI

Python 3.11 以降が必要です。外部ライブラリへの依存はありません。以下のいずれかの方法でインストールします。

```
uv tool install git+https://github.com/GeniusJunP/fusion-macro-cli
```

```
pipx install git+https://github.com/GeniusJunP/fusion-macro-cli
```

```
pip install git+https://github.com/GeniusJunP/fusion-macro-cli
```

これにより、`fusion-macro-cli` コマンドが利用可能になります。

### 3. DaVinci Resolve

`schema dump` および `preview` コマンドは、DaVinci Resolve Studio のスクリプティング API を使用します。Resolve の環境設定で外部スクリプティングを有効にしてください。無償版（Free）では外部スクリプティングが利用できないため、`--console` オプションを指定し、Fusion ページのコンソールから実行します。その他のコマンドは、Resolve がインストールされていない環境でも動作します。

## 使い方

Fusion で見た目を制作した後、エージェントに次のように指示します。

```
このタイムラインのテロップを、色だけ変えられるタイトルプリセットにして。サムネイルも作って .drfx にまとめて。
```

エージェントは Skill に記載されたフローに沿って CLI を実行します。`preview` コマンドを実行すると Resolve 上に確認用のタイムラインが作成されますので、レンダリング結果やインスペクタの項目順を目視で確認してください。なお、`.drfx` を Resolve に再インストールした後は、Resolve を再起動してください。

## ドキュメント

ドキュメントは英語で記述されています。

| ドキュメント | 内容 |
|---|---|
| [skills/fusion-macro-cli/SKILL.md](skills/fusion-macro-cli/SKILL.md) | 全体フロー、用語定義、形式の選択基準、公開入力がインスペクタに表示される条件 |
| [references/commands.md](skills/fusion-macro-cli/references/commands.md) | 各コマンドの入力・出力、オプション、動作仕様、終了コード |
| [references/declaration.md](skills/fusion-macro-cli/references/declaration.md) | 宣言ファイル（TOML）の書式仕様 |
| [references/rules.md](skills/fusion-macro-cli/references/rules.md) | 検査ルールが検出する内容、Resolve 上での影響、`fix` による自動修正可否 |
| [references/resolve.md](skills/fusion-macro-cli/references/resolve.md) | 実機検証に基づく DaVinci Resolve / Fusion の挙動 |
| [docs/development.md](docs/development.md) | 内部データ構造、モジュール構成、ドキュメント規約、テスト仕様 |

## ライセンス

[MIT](LICENSE)
