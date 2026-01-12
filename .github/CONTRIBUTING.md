# Contributing to cenima-cli

Thank you for considering contributing to cenima-cli! We welcome contributions from the community.

## 🚀 Getting Started

### Prerequisites

- Python 3.8 or higher
- `mpv` media player
- `fzf` fuzzy finder (optional but recommended)

### Development Setup

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/np4abdou1/cenima-cli.git
   cd cenima-cli
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -e ".[dev,api]"
   ```

4. **Run tests**
   ```bash
   pytest
   ```

## 📝 How to Contribute

### Reporting Bugs

If you find a bug, please create an issue with:
- Clear title and description
- Steps to reproduce
- Expected vs actual behavior
- Python version and OS
- Relevant logs or screenshots

### Suggesting Features

Feature requests are welcome! Please:
- Check existing issues first
- Describe the feature clearly
- Explain the use case
- Consider implementation details

### Submitting Pull Requests

1. **Create a feature branch**
   ```bash
   git checkout -b feature/amazing-feature
   ```

2. **Make your changes**
   - Follow existing code style
   - Add tests for new functionality
   - Update documentation as needed

3. **Run quality checks**
   ```bash
   # Format code
   black cenima/ tests/
   isort cenima/ tests/
   
   # Run tests
   pytest
   ```

4. **Commit your changes**
   ```bash
   git commit -m "Add amazing feature"
   ```
   
   Follow conventional commits:
   - `feat:` new features
   - `fix:` bug fixes
   - `docs:` documentation changes
   - `test:` test additions/changes
   - `refactor:` code refactoring
   - `chore:` maintenance tasks

5. **Push and create PR**
   ```bash
   git push origin feature/amazing-feature
   ```
   Then open a pull request on GitHub.

## 🎨 Code Style

- Follow [PEP 8](https://pep8.org/)
- Use type hints where appropriate
- Write docstrings for public functions/classes
- Keep functions focused and concise
- Maximum line length: 100 characters

## 🧪 Testing

- Write tests for new features
- Ensure all tests pass before submitting
- Aim for good test coverage
- Use meaningful test names

## 📚 Documentation

- Update README.md for user-facing changes
- Add docstrings to new functions/classes
- Update CHANGELOG.md for notable changes
- Keep code comments clear and helpful

## 🔍 Code Review Process

1. Automated checks must pass
2. At least one maintainer review required
3. Address feedback constructively
4. Be patient and respectful

## ⚖️ License

By contributing, you agree that your contributions will be licensed under the GPL-3.0 License.

## 💬 Questions?

Feel free to open a discussion or contact the maintainers!

---

Thank you for contributing to cenima-cli! 🎬
