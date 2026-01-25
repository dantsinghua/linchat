/**
 * sm-crypto 类型声明
 *
 * 国密算法库类型定义
 */
declare module 'sm-crypto' {
  interface SM4Options {
    mode?: 'ecb' | 'cbc';
    padding?: 'none' | 'pkcs#5' | 'pkcs#7';
    iv?: string;
    output?: 'string' | 'array';
  }

  interface SM4 {
    encrypt(data: string, key: string, options?: SM4Options): string;
    decrypt(data: string, key: string, options?: SM4Options): string;
  }

  interface SM3 {
    (data: string | ArrayLike<number>): string;
    digest(data: string | ArrayLike<number>): string;
  }

  interface SM2KeyPair {
    publicKey: string;
    privateKey: string;
  }

  interface SM2 {
    generateKeyPairHex(): SM2KeyPair;
    doEncrypt(data: string, publicKey: string, cipherMode?: number): string;
    doDecrypt(data: string, privateKey: string, cipherMode?: number): string;
    doSignature(
      data: string,
      privateKey: string,
      options?: { hash?: boolean; der?: boolean; userId?: string }
    ): string;
    doVerifySignature(
      data: string,
      signature: string,
      publicKey: string,
      options?: { hash?: boolean; der?: boolean; userId?: string }
    ): boolean;
  }

  export const sm4: SM4;
  export const sm3: SM3;
  export const sm2: SM2;
}
