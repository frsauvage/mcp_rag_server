import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser

code = '''
std::string fetchDataFromFile(std::string recvdData) {
    return \"File_\" + recvdData;
}
'''

parser = Parser(Language(tscpp.language()))
tree = parser.parse(code.encode())

def print_tree(node, indent=0):
    print('  ' * indent + f'{node.type}')
    for child in node.children:
        print_tree(child, indent + 1)

print_tree(tree.root_node)